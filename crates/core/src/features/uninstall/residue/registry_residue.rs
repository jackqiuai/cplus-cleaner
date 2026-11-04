use crate::{common, *};
use anyhow::{Result, bail};
use cplus_platform_windows::registry::{self, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, Key};
use std::collections::BTreeSet;

pub fn component(value: &str) -> bool {
    !value.is_empty()
        && !value.ends_with(['.', ' '])
        && !value.contains(['\\', '/', ':', '*', '?', '"', '<', '>', '|', '\0'])
        && ![
            ".",
            "..",
            "microsoft",
            "windows",
            "software",
            "programs",
            "program files",
            "program files (x86)",
            "common files",
            "appdata",
            "roaming",
            "local",
            "temp",
            "installer",
            "users",
            "system32",
            "syswow64",
        ]
        .contains(&value.to_lowercase().as_str())
}
type Values = Vec<(String, u32, Vec<u8>)>;
fn snapshot(key: &Key) -> Result<(String, Values, bool)> {
    let mut values = key.values();
    values.sort_by(|a, b| a.0.cmp(&b.0));
    if values.iter().map(|v| v.2.len()).sum::<usize>() > 16 * 1024 * 1024 {
        bail!("Registry residue is too large to verify");
    }
    let mut children = key.subkeys();
    children.sort();
    let blocked = !children.is_empty();
    let digest = common::hash(&serde_json::to_vec(&(&values, &children))?);
    Ok((digest, values, blocked))
}
pub fn discover(
    app: &AppInfo,
    others: &[registry::InstalledRecord],
) -> Vec<(RegistryResidue, Category)> {
    let leaf = app.path.file_name().and_then(|s| s.to_str()).unwrap_or("");
    if !component(leaf) {
        return vec![];
    }
    if others.iter().any(|r| {
        super::super::location(r)
            .file_name()
            .is_some_and(|n| n.eq_ignore_ascii_case(leaf))
    }) {
        return vec![];
    }
    let path = format!("SOFTWARE\\{leaf}");
    let mut rows = vec![];
    for (hive, name) in [(HKEY_CURRENT_USER, "HKCU"), (HKEY_LOCAL_MACHINE, "HKLM")] {
        for view in [registry::KEY_WOW64_32KEY, registry::KEY_WOW64_64KEY] {
            let Ok(key) = Key::open(hive, &path, view, false) else {
                continue;
            };
            let Ok((digest, values, blocked)) = snapshot(&key) else {
                continue;
            };
            let id = common::hash(format!("residue:{name}:{view}:{path}").as_bytes());
            rows.push((
                RegistryResidue {
                    id: id.clone(),
                    hive: name.into(),
                    view,
                    path: path.clone(),
                    digest,
                    values,
                    blocked,
                },
                Category {
                    id,
                    name: app.name.clone(),
                    group: "Registry residues".into(),
                    status: format!("{name}\\{path}"),
                    count: 1,
                    view_only: blocked,
                    ..Default::default()
                },
            ));
        }
    }
    rows
}
pub fn delete(rows: &[RegistryResidue], selected: &[String]) -> OperationResult {
    let mut result = OperationResult::default();
    let mut removed = BTreeSet::new();
    for row in rows.iter().filter(|r| selected.contains(&r.id)) {
        let attempt = (|| -> Result<()> {
            if row.blocked {
                bail!("Registry keys containing child keys are view only");
            }
            let Some(leaf) = row.path.strip_prefix("SOFTWARE\\").filter(|s| component(s)) else {
                bail!("Registry key is outside the residue scope");
            };
            let hive = match row.hive.as_str() {
                "HKCU" => HKEY_CURRENT_USER,
                "HKLM" => HKEY_LOCAL_MACHINE,
                _ => bail!("Unknown registry hive"),
            };
            if ![registry::KEY_WOW64_32KEY, registry::KEY_WOW64_64KEY].contains(&row.view) {
                bail!("Invalid registry view");
            }
            let key = match Key::open(hive, &row.path, row.view, true) {
                Ok(key) => key,
                Err(error)
                    if error.raw_os_error() == Some(2)
                        && removed.contains(&(row.hive.clone(), row.path.clone())) =>
                {
                    return Ok(());
                }
                Err(error) => return Err(error.into()),
            };
            let (digest, _, blocked) = snapshot(&key)?;
            if blocked || digest != row.digest {
                bail!("Registry residue changed after scanning; scan again");
            }
            for (name, _, _) in &row.values {
                key.delete_value(name)?;
            }
            drop(key);
            Key::open(hive, "SOFTWARE", row.view, true)?.delete_child(leaf)?;
            removed.insert((row.hive.clone(), row.path.clone()));
            Ok(())
        })();
        match attempt {
            Ok(()) => {
                result.count += 1;
                result.succeeded.push(row.id.clone());
            }
            Err(error) => result.errors.push(Issue {
                path: format!("{}\\{}", row.hive, row.path),
                message: error.to_string(),
            }),
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn registry_cleanup_is_non_recursive_and_snapshot_checked() {
        let leaf = format!("CPlusCleanerOwnedTest{}", common::token());
        let path = format!("SOFTWARE\\{leaf}");
        let key = Key::create(HKEY_CURRENT_USER, &path).unwrap();
        key.set_string("Owned", "before").unwrap();
        let (digest, values, blocked) = snapshot(&key).unwrap();
        let row = RegistryResidue {
            id: common::token(),
            hive: "HKCU".into(),
            view: registry::KEY_WOW64_64KEY,
            path: path.clone(),
            digest,
            values,
            blocked,
        };
        key.set_string("Owned", "changed").unwrap();
        assert_eq!(
            delete(std::slice::from_ref(&row), std::slice::from_ref(&row.id)).count,
            0
        );
        key.set_string("Owned", "before").unwrap();
        drop(key);
        let result = delete(std::slice::from_ref(&row), std::slice::from_ref(&row.id));
        assert!(result.ok(), "{:?}", result.errors);
        assert!(Key::open(HKEY_CURRENT_USER, &path, registry::KEY_WOW64_64KEY, false).is_err());
        assert!(!component("Microsoft"));
        assert!(!component("A\\B"));
    }
}
