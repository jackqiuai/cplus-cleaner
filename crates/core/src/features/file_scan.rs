use crate::common;
use anyhow::Result;
use cplus_platform_windows::filesystem as winfs;
use std::path::{Path, PathBuf};

pub(crate) struct Policy {
    roots: Vec<String>,
    executable: String,
    volume: String,
}

impl Policy {
    pub(crate) fn new(root: &Path, installed: &[PathBuf]) -> Result<Self> {
        let volume = PathBuf::from(winfs::disk(root)?.root);
        let mut roots = installed
            .iter()
            .filter(|path| path.is_absolute() && path.parent().is_some())
            .map(|p| common::key(p))
            .collect::<Vec<_>>();
        for key in [
            "SystemRoot",
            "ProgramFiles",
            "ProgramFiles(x86)",
            "ProgramData",
        ] {
            if let Some(path) = std::env::var_os(key) {
                roots.push(common::key(Path::new(&path)));
            }
        }
        for name in [
            "Windows",
            "Program Files",
            "Program Files (x86)",
            "ProgramData",
            "System Volume Information",
            "$Recycle.Bin",
            "Recovery",
            "$WinREAgent",
            "$Windows.~BT",
            "$Windows.~WS",
            "Windows.old",
            "Config.Msi",
            "MSOCache",
            "PerfLogs",
        ] {
            roots.push(common::key(&volume.join(name)));
        }
        if cfg!(debug_assertions) {
            roots.push(common::key(
                &PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../..")
                    .canonicalize()?,
            ));
        }
        roots.sort();
        roots.dedup();
        Ok(Self {
            roots,
            executable: std::env::current_exe()
                .map(|p| common::key(&p))
                .unwrap_or_default(),
            volume: common::key(&volume),
        })
    }

    pub(crate) fn excludes(&self, path: &Path) -> bool {
        let key = common::key(path);
        if key == self.executable
            || self.roots.iter().any(|root| {
                key == *root
                    || key
                        .strip_prefix(root)
                        .is_some_and(|suffix| suffix.starts_with('\\'))
            })
        {
            return true;
        }
        path.parent()
            .is_some_and(|parent| common::key(parent) == self.volume)
            && path.file_name().is_some_and(|name| {
                [
                    "hiberfil.sys",
                    "pagefile.sys",
                    "swapfile.sys",
                    "dumpstack.log.tmp",
                ]
                .iter()
                .any(|reserved| name.to_string_lossy().eq_ignore_ascii_case(reserved))
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn protection_uses_exact_roots_instead_of_company_name_globs() {
        let policy = Policy {
            roots: vec![r"c:\windows".into(), r"c:\program files".into()],
            executable: r"c:\tools\cleaner.exe".into(),
            volume: "c:".into(),
        };
        assert!(policy.excludes(Path::new(r"C:\Windows\large.bin")));
        assert!(policy.excludes(Path::new(r"C:\pagefile.sys")));
        assert!(!policy.excludes(Path::new(r"C:\Users\Jack\MicrosoftNotes\large.log")));
        assert!(!policy.excludes(Path::new(r"C:\Users\Jack\pagefile.sys")));
        assert!(!policy.excludes(Path::new(r"C:\WindowsBackup\large.bin")));
    }
}
