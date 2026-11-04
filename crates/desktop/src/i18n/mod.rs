use std::collections::BTreeMap;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Language {
    Chinese,
    Traditional,
    English,
}
impl Language {
    pub const ALL: [Self; 3] = [Self::Chinese, Self::Traditional, Self::English];
    pub fn code(self) -> &'static str {
        match self {
            Self::Chinese => "zh",
            Self::Traditional => "zh-TW",
            Self::English => "en",
        }
    }
    pub fn from_code(code: &str) -> Self {
        match code {
            "zh" => Self::Chinese,
            "zh-TW" => Self::Traditional,
            _ => Self::English,
        }
    }
}
impl std::fmt::Display for Language {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Chinese => "简体中文",
            Self::Traditional => "繁體中文",
            Self::English => "English",
        })
    }
}
pub struct I18n {
    pub language: Language,
    en: BTreeMap<String, String>,
    tw: BTreeMap<String, String>,
    ui: BTreeMap<String, String>,
}
impl I18n {
    pub fn new(language: Language) -> Self {
        let load = |path| {
            cplus_core::resource_json(path)
                .ok()
                .and_then(|v| serde_json::from_value(v["messages"].clone()).ok())
                .unwrap_or_default()
        };
        let mut en: BTreeMap<String, String> = load("locales/en.json");
        let mut ui: BTreeMap<String, String> = cplus_core::resource_json("locales/rust-ui.json")
            .ok()
            .and_then(|v| serde_json::from_value(v).ok())
            .unwrap_or_default();
        let errors: BTreeMap<String, String> =
            cplus_core::resource_json("locales/rust-errors.json")
                .ok()
                .and_then(|v| serde_json::from_value(v).ok())
                .unwrap_or_default();
        ui.extend(errors);
        en.extend(ui.iter().map(|(en, zh)| (zh.clone(), en.clone())));
        Self {
            language,
            en,
            tw: load("locales/zh-TW.json"),
            ui,
        }
    }
    pub fn t(&self, value: &str) -> String {
        let chinese = self.ui.get(value).map(String::as_str).unwrap_or_else(|| {
            self.en
                .iter()
                .find_map(|(zh, en)| if en == value { Some(zh.as_str()) } else { None })
                .unwrap_or(value)
        });
        match self.language {
            Language::English => {
                if matches!(value, "Delete selected" | "Delete record") {
                    "Delete".into()
                } else {
                    self.en.get(value).cloned().unwrap_or_else(|| value.into())
                }
            }
            Language::Chinese => chinese.into(),
            Language::Traditional => self
                .tw
                .get(chinese)
                .cloned()
                .unwrap_or_else(|| cplus_platform_windows::shell::traditional(chinese)),
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn approved_scan_and_delete_labels_are_unambiguous() {
        let zh = I18n::new(Language::Chinese);
        let en = I18n::new(Language::English);
        assert_eq!(zh.t("Scan"), "扫描");
        assert_eq!(zh.t("Drive Cleanup"), "盘清理");
        assert_eq!(zh.t("Delete"), "立即清理");
        assert_eq!(en.t("Delete selected"), "Delete");
        assert_eq!(en.t("Choose folder"), "Choose folder");
        assert_eq!(zh.t("Destination already exists"), "目标位置已存在");
    }
    #[test]
    fn traditional_labels_use_pan_instead_of_cao() {
        let tw = I18n::new(Language::Traditional);
        assert_eq!(tw.t("Drive Cleanup"), "盤清理");
        assert_eq!(tw.t("System Slimming"), "系統瘦身");
    }
}
