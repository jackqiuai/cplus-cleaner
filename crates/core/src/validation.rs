use anyhow::{Result, bail};
pub fn bundle_report() -> Result<serde_json::Value> {
    let required = [
        "assets/brand/logo.svg",
        "assets/app-icon.png",
        "assets/tray.ico",
        "assets/check-on.png",
        "assets/check-off.png",
        "assets/donation/wechat.png",
        "assets/icon-cache.png",
        "assets/icon-wechat.png",
        "assets/icon-qq.png",
        "assets/popup-huorong.json",
        "assets/chat-rules.json",
        "rules/drive-reviewed.json",
        "locales/en.json",
        "locales/zh-TW.json",
        "locales/rust-ui.json",
        "locales/rust-errors.json",
        "assets/ui/empty-file.svg",
        "assets/ui/result-check-on.svg",
        "assets/ui/result-check-off.svg",
        "assets/ui/result-check-partial.svg",
        "assets/ui/result-folder.svg",
        "assets/ui/result-file.svg",
        "assets/ui/result-copy.svg",
        "assets/ui/result-open-folder.svg",
        "assets/ui/home-folder.svg",
        "assets/ui/home-hero-waves.svg",
        "assets/ui/application.svg",
        "assets/ui/recycle_bin.svg",
        "assets/ui/hibernation.svg",
        "assets/ui/pagefile.svg",
        "assets/ui/swapfile.svg",
        "assets/ui/components.svg",
    ];
    for key in required {
        if crate::resource(key).is_none_or(|b| b.is_empty()) {
            bail!("Missing runtime resource: {key}");
        }
    }
    let drive = crate::resource_json("rules/drive-reviewed.json")?;
    for c in drive["categories"].as_array().into_iter().flatten() {
        if let Some(icon) = c["icon"].as_str().filter(|s| !s.is_empty()) {
            let path = format!("assets/{}", icon.trim_start_matches("assets/"));
            if crate::resource(&path).is_none() {
                bail!("Missing category image: {path}");
            }
        }
    }
    let popup = crate::resource_json("assets/popup-huorong.json")?;
    let chat = crate::resource_json("assets/chat-rules.json")?;
    for language in ["locales/en.json", "locales/zh-TW.json"] {
        let value = crate::resource_json(language)?;
        if value["messages"].as_object().is_none_or(|o| o.is_empty()) {
            bail!("Empty language resource: {language}");
        }
    }
    Ok(
        serde_json::json!({"ok":true,"version":crate::VERSION,"runtime":"Rust + Iced","features":crate::Feature::ALL,"languages":["zh","zh-TW","en"],"reviewed_drive_categories":drive["categories"].as_array().map(Vec::len),"popup_definitions":popup["groups"].as_array().map(|g|g.iter().map(|v|v["windows"].as_array().map(Vec::len).unwrap_or(0)).sum::<usize>()),"chat_applications":chat["applications"].as_array().map(Vec::len)}),
    )
}
#[cfg(test)]
mod tests {
    #[test]
    fn runtime_bundle_contains_rule_images_and_languages() {
        let report = super::bundle_report().unwrap();
        assert_eq!(report["ok"], true);
        assert_eq!(report["chat_applications"], 8);
        assert_eq!(report["features"].as_array().unwrap().len(), 11);
    }
}
