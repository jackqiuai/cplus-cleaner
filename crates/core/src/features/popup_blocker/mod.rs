use crate::{common, jobs::Context, *};
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::{
    registry::{self, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, Key},
    shell,
    windows::{self, Window},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Group {
    pub key: String,
    pub name: String,
    pub roots: Vec<PathBuf>,
    pub rules: Vec<PopupRule>,
    pub icon: Option<shell::Icon>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PopupRule {
    pub id: String,
    pub label: String,
    pub action: String,
    pub position: i32,
    #[serde(skip)]
    matcher: Option<Matcher>,
}
#[derive(Clone, Debug)]
struct Matcher {
    contexts: Vec<Vec<Condition>>,
    styles: Option<u32>,
    exstyles: Option<u32>,
    position: i32,
    sizes: Vec<(String, Vec<Vec<i32>>)>,
    points: Vec<[u32; 4]>,
}
#[derive(Clone, Debug)]
struct Condition {
    field: String,
    literal: String,
    regular: Option<regex::Regex>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct State {
    pub enabled: bool,
    pub software: BTreeMap<String, bool>,
    pub rules: BTreeMap<String, bool>,
    pub counts: BTreeMap<String, u64>,
    pub history: BTreeMap<String, Vec<Value>>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
struct Cache {
    version: u32,
    groups: Vec<CachedGroup>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct CachedGroup {
    key: String,
    roots: Vec<PathBuf>,
    rules: Vec<String>,
}

fn id(value: &str) -> String {
    format!("huorong:{}", &common::hash(value.as_bytes())[..24])
}
fn python_json(v: &Value) -> String {
    match v {
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| format!("{}: {}", serde_json::to_string(k).unwrap(), python_json(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Array(a) => format!(
            "[{}]",
            a.iter().map(python_json).collect::<Vec<_>>().join(", ")
        ),
        _ => v.to_string(),
    }
}
fn name_matches(pattern: &str, value: &str) -> bool {
    let mut r = String::from("(?is)^(");
    for c in pattern.chars() {
        match c {
            '*' => r.push_str(".*"),
            '?' => r.push('.'),
            '>' => r.push_str(r"[^\\]*"),
            _ => r.push_str(&regex::escape(&c.to_string())),
        }
    }
    r.push_str(")$");
    regex::Regex::new(&r).is_ok_and(|re| re.is_match(value))
}
fn expand(value: &str, root: &Path, regular: bool) -> Result<String> {
    let re = regex::Regex::new(r"%([^%]+)%").unwrap();
    let mut error = false;
    let v = re.replace_all(value, |c: &regex::Captures| {
        let value = if c[1].eq_ignore_ascii_case("INSTDIR") {
            root.display()
                .to_string()
                .trim_start_matches(r"\\?\")
                .to_string()
        } else {
            common::expand_env(&c[0]).unwrap_or_default()
        };
        if value.is_empty() {
            error = true;
        }
        if regular {
            regex::escape(value.trim_end_matches(['\\', '/']))
        } else {
            value.trim_end_matches(['\\', '/']).into()
        }
    });
    if error {
        bail!("Unresolved popup directory");
    }
    Ok(v.into_owned())
}
fn regex(value: &str) -> Result<regex::Regex> {
    if value.len() > 2048 || value.contains('\0') {
        bail!("Invalid popup expression");
    }
    let mut translated = String::new();
    let (mut escape, mut class) = (false, false);
    for c in value.chars() {
        if escape {
            let ascii = match c {
                'd' => Some("[0-9]"),
                'D' => Some("[^0-9]"),
                'w' => Some("[A-Za-z0-9_]"),
                'W' => Some("[^A-Za-z0-9_]"),
                's' => Some(r"[ \t\r\n\x0b\x0c]"),
                'S' => Some(r"[^ \t\r\n\x0b\x0c]"),
                'b' if !class => Some(r"(?-u:\b)"),
                'B' if !class => Some(r"(?-u:\B)"),
                _ => None,
            };
            if let Some(ascii) = ascii {
                translated.pop();
                translated.push_str(ascii);
            } else {
                translated.push(c);
            }
            escape = false;
        } else {
            match c {
                '\\' => {
                    translated.push(c);
                    escape = true;
                }
                '[' => {
                    class = true;
                    translated.push(c);
                }
                ']' => {
                    class = false;
                    translated.push(c);
                }
                '.' if !class => translated.push_str(r"[^\r\n\x{2028}\x{2029}]"),
                _ => translated.push(c),
            }
        }
    }
    Ok(regex::RegexBuilder::new(&format!("^(?:{translated})$"))
        .unicode(true)
        .size_limit(256 * 1024)
        .build()?)
}
fn size(value: &Value, length: Option<usize>) -> Result<Vec<i32>> {
    let v = value
        .as_str()
        .context("Invalid popup size")?
        .split(',')
        .map(|n| n.trim().parse::<i32>())
        .collect::<std::result::Result<Vec<_>, _>>()?;
    if ![2, 4].contains(&v.len())
        || length.is_some_and(|n| n != v.len())
        || v.iter().any(|n| *n < 0 || *n > 65535)
        || (v.len() == 4 && (v[0] > v[2] || v[1] > v[3]))
    {
        bail!("Invalid popup dimensions");
    }
    Ok(v)
}
fn compile(scan: &Value, roots: &[PathBuf]) -> Result<Matcher> {
    let object = scan.as_object().context("Missing popup scan")?;
    let allowed = [
        "procname",
        "procname_rx",
        "class",
        "class_rx",
        "caption",
        "caption_rx",
        "styles",
        "exstyles",
        "pos",
        "size",
        "varsize",
        "include_size",
        "exclude_size",
        "image",
        "comment",
    ];
    if object.is_empty()
        || object.keys().any(|k| !allowed.contains(&k.as_str()))
        || roots.is_empty()
    {
        bail!("Unsupported popup condition");
    }
    let number = |key: &str| -> Result<Option<u32>> {
        match scan.get(key) {
            None => Ok(None),
            Some(v) => {
                let n = v.as_i64().context("Invalid popup style")?;
                if !(-2147483648..=4294967295).contains(&n) {
                    bail!("Invalid popup style");
                }
                Ok(Some(n as u32))
            }
        }
    };
    let position = scan
        .get("pos")
        .map(|v| v.as_i64().context("Invalid position"))
        .transpose()?
        .unwrap_or(-1) as i32;
    if !(-1..=5).contains(&position) {
        bail!("Invalid popup position");
    }
    let mut sizes = vec![];
    for key in ["size", "varsize", "include_size", "exclude_size"] {
        if let Some(v) = scan.get(key) {
            let entries = if key.contains("_size") {
                v.as_array()
                    .context("Invalid popup size list")?
                    .iter()
                    .map(|v| size(v, None))
                    .collect::<Result<Vec<_>>>()?
            } else {
                vec![size(v, Some(if key == "size" { 2 } else { 4 }))?]
            };
            sizes.push((key.into(), entries));
        }
    }
    let mut contexts = vec![];
    for root in roots {
        let mut context = vec![];
        for (key, field) in [
            ("procname", "image"),
            ("class", "class"),
            ("caption", "title"),
        ] {
            for regular in [false, true] {
                let name = if regular {
                    format!("{key}_rx")
                } else {
                    key.into()
                };
                if let Some(v) = scan.get(&name) {
                    let value = expand(
                        v.as_str().context("Invalid popup condition")?,
                        root,
                        regular && field == "image",
                    )?;
                    if field == "image"
                        && !regular
                        && (!Path::new(&value).is_absolute()
                            || !roots.iter().any(|r| common::inside(Path::new(&value), r)))
                    {
                        bail!("Popup process is outside the installed application");
                    }
                    context.push(Condition {
                        field: field.into(),
                        regular: if regular { Some(regex(&value)?) } else { None },
                        literal: value,
                    });
                }
            }
        }
        if !context.iter().any(|c| c.field == "image") {
            bail!("Unbound popup process");
        }
        contexts.push(context);
    }
    let mut points = vec![];
    if let Some(image) = scan.get("image") {
        let map = image.as_object().context("Invalid pixel conditions")?;
        if map.keys().any(|k| k != "points") {
            bail!("Unknown pixel condition");
        }
        if let Some(p) = image.get("points") {
            let p = p.as_array().context("Invalid pixel list")?;
            if p.len() > 256 {
                bail!("Too many pixel conditions");
            }
            for p in p {
                if p.as_object().is_none_or(|m| m.len() != 4) {
                    bail!("Invalid pixel condition");
                }
                let mut values = [0; 4];
                for (i, key) in ["x", "y", "color", "type"].iter().enumerate() {
                    values[i] = u32::try_from(p[key].as_u64().context("Invalid pixel value")?)?;
                }
                if values[0] > 16384 || values[1] > 16384 || values[3] > 1 {
                    bail!("Invalid pixel condition");
                }
                points.push(values);
            }
        }
    }
    Ok(Matcher {
        contexts,
        styles: number("styles")?,
        exstyles: number("exstyles")?,
        position,
        sizes,
        points,
    })
}
impl Matcher {
    fn matches(&self, w: &Window) -> bool {
        for (expected, actual) in [(self.styles, w.styles), (self.exstyles, w.exstyles)] {
            if let Some(expected) = expected
                && actual != expected
                && (actual & expected) == 0
            {
                return false;
            }
        }
        if self.position != -1 && self.position != w.pos {
            return false;
        }
        let width = w.width * 100 / w.scale.max(1) as i32;
        let height = w.height * 100 / w.scale.max(1) as i32;
        let hit = |s: &Vec<i32>| {
            if s.len() == 2 {
                width == s[0] && height == s[1]
            } else {
                width >= s[0] && height >= s[1] && width <= s[2] && height <= s[3]
            }
        };
        for (kind, sizes) in &self.sizes {
            let yes = sizes.iter().any(&hit);
            if (kind == "exclude_size" && yes) || (kind != "exclude_size" && !yes) {
                return false;
            }
        }
        if !self.contexts.iter().any(|c| {
            c.iter().all(|c| {
                let actual = match c.field.as_str() {
                    "image" => &w.image,
                    "class" => &w.class,
                    _ => &w.title,
                };
                if actual.len() > 4096 {
                    return false;
                }
                if let Some(re) = &c.regular {
                    !actual.chars().any(|c| c as u32 > 65535) && re.is_match(actual)
                } else {
                    if c.field == "image" {
                        common::key(Path::new(actual)) == common::key(Path::new(&c.literal))
                    } else {
                        actual.to_lowercase() == c.literal.to_lowercase()
                    }
                }
            })
        }) {
            return false;
        }
        self.points.iter().all(|p| {
            windows::pixel(
                w,
                (p[0] * w.scale / 100) as i32,
                (p[1] * w.scale / 100) as i32,
            )
            .is_some_and(|n| (n == p[2]) == (p[3] == 0))
        })
    }
}
fn roots(row: &registry::InstalledRecord) -> Vec<PathBuf> {
    let mut roots = vec![super::uninstall::location(row)];
    if row.msi {
        roots.push(PathBuf::from(registry::msi_property(
            &row.key,
            "InstallLocation",
        )));
    }
    if let Some(p) = super::uninstall::executable(&row.icon)
        && let Some(parent) = p.parent()
    {
        roots.push(parent.into());
    }
    roots
        .into_iter()
        .filter(|p| {
            p.is_absolute()
                && p.is_dir()
                && !super::uninstall::is_shared(p)
                && !p.to_string_lossy().starts_with(r"\\")
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}
fn regval(v: &Value) -> Vec<PathBuf> {
    let mut roots = vec![];
    let Some(values) = v.as_object() else {
        return roots;
    };
    if values
        .keys()
        .any(|k| !["regkey64", "regkey", "valuename"].contains(&k.as_str()))
    {
        return roots;
    }
    for (key, view) in [
        ("regkey", registry::KEY_WOW64_32KEY),
        ("regkey64", registry::KEY_WOW64_64KEY),
    ] {
        let Some(path) = v[key].as_str() else {
            continue;
        };
        let Some((hive, path)) = path.split_once('\\') else {
            continue;
        };
        let hive = match hive {
            "HKEY_CURRENT_USER" => HKEY_CURRENT_USER,
            "HKEY_LOCAL_MACHINE" => HKEY_LOCAL_MACHINE,
            _ => continue,
        };
        if let Ok(key) = Key::open(hive, path, view, false)
            && let Ok(path) = common::expand_env(&key.string(v["valuename"].as_str().unwrap_or("")))
        {
            roots.push(path.trim_matches('"').into());
        }
    }
    roots
}
fn discover(group: &Value, records: &[registry::InstalledRecord]) -> Vec<PathBuf> {
    let Some(exist) = group["exist"].as_object() else {
        return vec![];
    };
    if exist.is_empty()
        || exist
            .keys()
            .any(|k| !["appname", "appkey", "file_exist", "alway"].contains(&k.as_str()))
    {
        return vec![];
    }
    let Some(directory) = group["instdir"].as_object() else {
        return vec![];
    };
    if directory.len() != 1 {
        return vec![];
    }
    let matches = |r: &&registry::InstalledRecord| -> bool {
        exist.iter().all(|(k, v)| match k.as_str() {
            "appname" => {
                let pattern = v.as_str().unwrap_or("");
                name_matches(pattern, &r.name)
                    || (directory
                        .get("appkey")
                        .and_then(Value::as_str)
                        .is_some_and(|p| name_matches(p, &r.key))
                        && r.name.starts_with(pattern)
                        && r.name[pattern.len()..]
                            .chars()
                            .all(|c| c.is_ascii_digit() || c == '.' || c.is_whitespace()))
            }
            "appkey" => name_matches(v.as_str().unwrap_or(""), &r.key),
            _ => true,
        })
    };
    let matched = records.iter().filter(matches).collect::<Vec<_>>();
    if (exist.contains_key("appname") || exist.contains_key("appkey")) && matched.is_empty() {
        return vec![];
    }
    let mut found = vec![];
    for (k, v) in directory {
        match k.as_str() {
            "appname" | "appkey" => {
                for row in records {
                    if name_matches(
                        v.as_str().unwrap_or(""),
                        if k == "appname" { &row.name } else { &row.key },
                    ) {
                        found.extend(roots(row));
                    }
                }
            }
            "regval" => found.extend(regval(v)),
            "file_path" => {
                if let Ok(path) = expand(v.as_str().unwrap_or(""), Path::new(""), false) {
                    found.push(path.into());
                }
            }
            _ => return vec![],
        }
    }
    let mut found = found
        .into_iter()
        .filter(|p| p.is_absolute() && p.is_dir() && !super::uninstall::is_shared(p))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    if let Some(pattern) = exist.get("file_exist").and_then(Value::as_str) {
        found.retain(|p| expand(pattern, p, false).is_ok_and(|v| Path::new(&v).exists()));
    }
    found
}
fn group_icon(
    definition: &Value,
    directories: &[PathBuf],
    records: &[registry::InstalledRecord],
    cache: &mut super::uninstall::IconCache,
) -> Option<shell::Icon> {
    if let Some(path) = definition["display_icon"]["file_path"].as_str() {
        for root in directories {
            if let Ok(icon) = expand(path, root, false)
                && let Some(image) = super::uninstall::registered_icon(
                    &registry::InstalledRecord {
                        icon,
                        ..Default::default()
                    },
                    cache,
                )
            {
                return Some(image);
            }
        }
    }
    if let Some(pattern) = definition["display_icon"]["appname"].as_str()
        && let Some(image) = records
            .iter()
            .filter(|r| name_matches(pattern, &r.name))
            .find_map(|r| super::uninstall::registered_icon(r, cache))
    {
        return Some(image);
    }
    if let Some(image) = records
        .iter()
        .filter(|r| roots(r).iter().any(|root| directories.contains(root)))
        .find_map(|r| super::uninstall::registered_icon(r, cache))
    {
        return Some(image);
    }
    let row = registry::InstalledRecord {
        name: definition["name"].as_str().unwrap_or_default().into(),
        ..Default::default()
    };
    directories
        .iter()
        .find_map(|root| super::uninstall::application_icon(&row, root, cache))
}

fn build(
    group: &Value,
    roots: Vec<PathBuf>,
    labels: &Value,
    selected: Option<&[String]>,
    records: &[registry::InstalledRecord],
    icons: &mut super::uninstall::IconCache,
) -> Group {
    let key = id(&format!("group:{}", group["name"].as_str().unwrap_or("")));
    let name = labels[group["display_name"].as_str().unwrap_or("")]
        .as_str()
        .or(group["name"].as_str())
        .unwrap_or("")
        .into();
    let mut result = Group {
        key,
        name,
        roots: roots.clone(),
        ..Default::default()
    };
    let mut occurrence = BTreeMap::new();
    for window in group["windows"].as_array().into_iter().flatten() {
        let name = window["name"].as_str().unwrap_or("");
        let n = occurrence.entry(name).or_insert(0u64);
        let identity = id(&format!(
            "{}:{name}:{}:{}",
            group["name"].as_str().unwrap_or(""),
            *n,
            python_json(window)
        ));
        *n += 1;
        if selected.is_some_and(|s| !s.contains(&identity)) {
            continue;
        }
        if window.as_object().is_none_or(|m| {
            m.keys()
                .any(|k| !["name", "scan", "display_name", "treat"].contains(&k.as_str()))
        }) {
            continue;
        }
        let action = match window["treat"].as_str().unwrap_or("") {
            "close" => "close",
            "hide" => "hide",
            "kill" => "kill",
            "" => "close_hide",
            _ => continue,
        };
        let Ok(matcher) = compile(&window["scan"], &roots) else {
            continue;
        };
        let label = labels[window["display_name"].as_str().unwrap_or("")]
            .as_str()
            .unwrap_or(name)
            .to_owned();
        result.rules.push(PopupRule {
            id: identity,
            label,
            action: action.into(),
            position: matcher.position,
            matcher: Some(matcher),
        });
    }
    result.icon = group_icon(group, &roots, records, icons);
    result
}
fn catalog() -> Result<Value> {
    crate::resource_json("assets/popup-huorong.json")
}
pub fn scan(store: &Store, ctx: &Context) -> Result<ScanResult> {
    let asset = catalog()?;
    let records = registry::installed()
        .into_iter()
        .filter(|r| {
            !r.system && r.parent.is_empty() && r.release_type.is_empty() && !r.uninstall.is_empty()
        })
        .collect::<Vec<_>>();
    let definitions = asset["groups"]
        .as_array()
        .context("Invalid popup catalog")?;
    let mut groups = vec![];
    let mut icons = super::uninstall::IconCache::new();
    for (i, definition) in definitions.iter().enumerate() {
        if ctx.cancel.cancelled() {
            break;
        }
        ctx.set(
            "popup",
            definition["name"].as_str().unwrap_or(""),
            i as u64,
            Some(definitions.len() as u64),
            0,
        );
        let admitted = discover(definition, &records);
        if admitted.is_empty() {
            continue;
        }
        let group = build(
            definition,
            admitted,
            &asset["labels"],
            None,
            &records,
            &mut icons,
        );
        if !group.rules.is_empty() {
            groups.push(group);
        }
    }
    let cache = Cache {
        version: 1,
        groups: groups
            .iter()
            .map(|g| CachedGroup {
                key: g.key.clone(),
                roots: g.roots.clone(),
                rules: g.rules.iter().map(|r| r.id.clone()).collect(),
            })
            .collect(),
    };
    if !ctx.cancel.cancelled() {
        store.write("popup/rust-results.json", &cache)?;
        let mut state = load_state(store);
        for g in &groups {
            state.software.insert(g.key.clone(), true);
            for r in &g.rules {
                state
                    .rules
                    .entry(r.id.clone())
                    .or_insert(r.action != "kill");
            }
        }
        state.enabled = groups
            .iter()
            .flat_map(|g| &g.rules)
            .any(|r| state.rules.get(&r.id) == Some(&true));
        save_state(store, &state)?;
    }
    Ok(ScanResult {
        feature: Feature::Popup,
        token: common::token(),
        popup: groups,
        cancelled: ctx.cancel.cancelled(),
        ..Default::default()
    })
}
pub fn restore(store: &Store) -> Result<Vec<Group>> {
    let cache = store
        .read::<Cache>("popup/rust-results.json")
        .or_else(|_| store.read::<Cache>("popup/results.json"))
        .unwrap_or_default();
    let asset = catalog()?;
    let mut groups = vec![];
    let records = if cache.groups.is_empty() {
        vec![]
    } else {
        registry::installed()
    };
    let mut icons = super::uninstall::IconCache::new();
    for entry in cache.groups.into_iter().take(1000) {
        let Some(definition) = asset["groups"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|g| id(&format!("group:{}", g["name"].as_str().unwrap_or(""))) == entry.key)
        else {
            continue;
        };
        let roots = entry
            .roots
            .into_iter()
            .filter(|p| p.is_absolute() && p.is_dir() && !super::uninstall::is_shared(p))
            .take(50)
            .collect::<Vec<_>>();
        let group = build(
            definition,
            roots,
            &asset["labels"],
            Some(&entry.rules),
            &records,
            &mut icons,
        );
        if !group.rules.is_empty() {
            groups.push(group);
        }
    }
    Ok(groups)
}
pub fn load_state(store: &Store) -> State {
    store.read("popup/state.json").unwrap_or_default()
}
pub fn save_state(store: &Store, state: &State) -> Result<()> {
    store.write("popup/state.json", state)
}
pub struct Engine {
    pub groups: Vec<Group>,
    pub state: State,
    pub error: String,
}
impl Engine {
    pub fn new(store: &Store) -> Self {
        let groups = restore(store).unwrap_or_default();
        let state = load_state(store);
        Self {
            groups,
            state,
            error: String::new(),
        }
    }
    pub fn toggle(&mut self, store: &Store, identity: &str, on: bool) -> Result<()> {
        if let Some(group) = self.groups.iter().find(|g| g.key == identity) {
            self.state.software.insert(identity.into(), on);
            for r in &group.rules {
                if r.action != "kill" {
                    self.state.rules.insert(r.id.clone(), on);
                }
            }
        } else if self
            .groups
            .iter()
            .flat_map(|g| &g.rules)
            .any(|r| r.id == identity)
        {
            self.state.rules.insert(identity.into(), on);
            if on {
                for g in &self.groups {
                    if g.rules.iter().any(|r| r.id == identity) {
                        self.state.software.insert(g.key.clone(), true);
                    }
                }
            }
        } else {
            bail!("Unknown popup rule");
        }
        self.state.enabled = self.groups.iter().any(|g| {
            self.state.software.get(&g.key) == Some(&true)
                && g.rules
                    .iter()
                    .any(|r| self.state.rules.get(&r.id) == Some(&true))
        });
        save_state(store, &self.state)
    }
    pub fn enabled(&self, id: &str) -> bool {
        self.state
            .rules
            .get(id)
            .copied()
            .or_else(|| self.state.software.get(id).copied())
            .unwrap_or(false)
    }
}
pub fn background(store: Store, engine: Arc<Mutex<Engine>>, stop: Arc<AtomicBool>) {
    if store.isolated {
        return;
    }
    std::thread::spawn(move || {
        let asset = catalog().unwrap_or_default();
        let protected = asset["sysclass"]
            .as_array()
            .into_iter()
            .flatten()
            .chain(
                asset["whitelist"]["whitelist"]
                    .as_array()
                    .into_iter()
                    .flatten(),
            )
            .filter_map(Value::as_str)
            .map(str::to_lowercase)
            .collect::<BTreeSet<_>>();
        while !stop.load(Ordering::Relaxed) {
            let (groups, state) = {
                let e = engine.lock().unwrap_or_else(|e| e.into_inner());
                (e.groups.clone(), e.state.clone())
            };
            if state.enabled {
                let windows = windows::enumerate();
                let mut hits = vec![];
                for window in &windows {
                    if protected.contains(&window.class.to_lowercase()) {
                        continue;
                    }
                    for group in &groups {
                        if !state.software.get(&group.key).copied().unwrap_or(false) {
                            continue;
                        }
                        if let Some(rule) = group.rules.iter().find(|r| {
                            state.rules.get(&r.id) == Some(&true)
                                && r.matcher.as_ref().is_some_and(|m| m.matches(window))
                        }) {
                            if windows::act(window, &rule.action).unwrap_or(false) {
                                hits.push((
                                    rule.id.clone(),
                                    group.name.clone(),
                                    rule.label.clone(),
                                ));
                            }
                            break;
                        }
                    }
                }
                if !hits.is_empty() {
                    let mut e = engine.lock().unwrap_or_else(|e| e.into_inner());
                    for (id, name, label) in hits {
                        *e.state.counts.entry(id.clone()).or_default() += 1;
                        let history = e.state.history.entry(id).or_default();
                        history.push(serde_json::json!({"time":chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),"software":name,"popup":label}));
                        if history.len() > 30 {
                            history.remove(0);
                        }
                    }
                    if let Err(err) = save_state(&store, &e.state) {
                        e.error = err.to_string();
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(700));
        }
    });
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cached_groups_restore_the_declared_nested_icon() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::new(Some(temp.path().join("data")), true).unwrap();
        let root = temp.path().join("app");
        std::fs::create_dir_all(root.join("Bin")).unwrap();
        let command =
            PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/cmd.exe");
        std::fs::copy(command, root.join("Bin/QQ.exe")).unwrap();
        let asset = catalog().unwrap();
        let definition = asset["groups"]
            .as_array()
            .unwrap()
            .iter()
            .find(|g| g["name"] == "qq")
            .unwrap();
        let group = build(
            definition,
            vec![root.clone()],
            &asset["labels"],
            None,
            &[],
            &mut super::super::uninstall::IconCache::new(),
        );
        assert!(!group.rules.is_empty());
        let expected = group.icon.unwrap();
        store
            .write(
                "popup/rust-results.json",
                &Cache {
                    version: 1,
                    groups: vec![CachedGroup {
                        key: group.key,
                        roots: vec![root],
                        rules: group.rules.into_iter().map(|r| r.id).collect(),
                    }],
                },
            )
            .unwrap();
        let restored = restore(&store).unwrap();
        assert_eq!(restored.len(), 1);
        assert_eq!(restored[0].icon.as_ref().unwrap().rgba, expected.rgba);
    }

    #[test]
    fn declared_application_icons_keep_the_index_and_try_all_matching_records() {
        let root = tempfile::tempdir().unwrap();
        let library =
            PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/shell32.dll");
        let records = [
            registry::InstalledRecord {
                name: "Fixture broken".into(),
                icon: root.path().join("missing.dll").display().to_string(),
                ..Default::default()
            },
            registry::InstalledRecord {
                name: "Fixture working".into(),
                icon: format!("\"{}\",4", library.display()),
                ..Default::default()
            },
        ];
        let mut cache = super::super::uninstall::IconCache::new();
        let image = group_icon(
            &serde_json::json!({"display_icon":{"appname":"Fixture*"}}),
            &[root.path().into()],
            &records,
            &mut cache,
        )
        .unwrap();
        assert_eq!(image.rgba, shell::icon(&library, 4).unwrap().rgba);
        assert!(cache.contains_key(&(common::key(&library), 4)));
    }

    #[test]
    fn fully_transparent_icons_fall_back_to_the_application() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("transparent.ico");
        let mut ico = vec![0u8; 22 + 40 + 32 * 32 * 4 + 32 * 4];
        ico[2..6].copy_from_slice(&[1, 0, 1, 0]);
        ico[6..8].copy_from_slice(&[32, 32]);
        ico[10..14].copy_from_slice(&[1, 0, 32, 0]);
        ico[14..18].copy_from_slice(&4264u32.to_le_bytes());
        ico[18..22].copy_from_slice(&22u32.to_le_bytes());
        ico[22..26].copy_from_slice(&40u32.to_le_bytes());
        ico[26..30].copy_from_slice(&32u32.to_le_bytes());
        ico[30..34].copy_from_slice(&64u32.to_le_bytes());
        ico[34..38].copy_from_slice(&[1, 0, 32, 0]);
        ico[42..46].copy_from_slice(&4224u32.to_le_bytes());
        ico[22 + 40 + 32 * 32 * 4..].fill(255);
        std::fs::write(&path, ico).unwrap();
        assert!(shell::icon(&path, 0).is_none());
        let command =
            PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/cmd.exe");
        std::fs::copy(command, temp.path().join("application.exe")).unwrap();
        let image = group_icon(
            &serde_json::json!({"display_icon":{"file_path":"%INSTDIR%\\transparent.ico"}}),
            &[temp.path().into()],
            &[],
            &mut super::super::uninstall::IconCache::new(),
        )
        .unwrap();
        assert!(image.rgba.as_chunks::<4>().0.iter().any(|p| p[3] != 0));
    }

    #[test]
    fn extended_length_install_roots_match_normal_process_images() {
        let root = PathBuf::from(r"\\?\C:\Fixture");
        let scan = serde_json::json!({"procname_rx":r"%INSTDIR%\\popup[0-9]+\.exe","caption":"Ad"});
        let matcher = compile(&scan, &[root]).unwrap();
        let window = Window {
            image: r"C:\Fixture\popup12.exe".into(),
            title: "Ad".into(),
            scale: 100,
            ..Default::default()
        };
        assert!(matcher.matches(&window));
    }
    #[test]
    fn ascii_regex_and_line_terminators_are_preserved() {
        assert!(regex(r"window\d+.*").unwrap().is_match("window12 caption"));
        assert!(!regex(r"window\d+").unwrap().is_match("window１２"));
        assert!(!regex("a.b").unwrap().is_match("a\rb"));
        assert!(regex(r"(a)\1").is_err());
    }
    #[test]
    fn unknown_fields_disable_the_entire_rule() {
        assert!(compile(&serde_json::json!({"procname":"C:\\Fixture\\pop.exe","new_unsupported_condition":true}),&[PathBuf::from(r"C:\Fixture")]).is_err());
    }
    #[test]
    fn all_window_conditions_must_match() {
        let m=compile(&serde_json::json!({"procname":"C:\\Fixture\\pop.exe","caption":"Ad","class":"Popup","size":"300,200","pos":5}),&[PathBuf::from(r"C:\Fixture")]).unwrap();
        let w = Window {
            image: r"C:\Fixture\pop.exe".into(),
            title: "Ad".into(),
            class: "Popup".into(),
            width: 300,
            height: 200,
            scale: 100,
            pos: 5,
            ..Default::default()
        };
        assert!(m.matches(&w));
        assert!(!m.matches(&Window {
            title: "Main document".into(),
            ..w
        }));
    }
    #[test]
    fn saved_cache_rebinds_only_shipped_definitions() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::new(Some(temp.path().into()), true).unwrap();
        store.write("popup/rust-results.json",&serde_json::json!({"version":1,"groups":[{"key":"invented","roots":[temp.path()],"rules":["invented"]}]})).unwrap();
        assert!(restore(&store).unwrap().is_empty());
    }
}
