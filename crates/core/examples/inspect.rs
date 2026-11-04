//! Read-only diagnostics. Never performs cleanup or enables popup interception.
use cplus_core::{Feature, Store, features::home, jobs::Context};
fn main() -> anyhow::Result<()> {
    let arg = std::env::args().nth(1).unwrap_or_else(|| "privacy".into());
    let feature = Feature::ALL
        .into_iter()
        .find(|f| f.key() == arg)
        .ok_or_else(|| anyhow::anyhow!("Unknown feature"))?;
    if matches!(
        feature,
        Feature::Home | Feature::Duplicates | Feature::Popup
    ) {
        anyhow::bail!("This diagnostic supports direct read-only scans only");
    }
    let store = Store::new(None, true)?;
    let report = home::scan_feature(feature, &store, None, 0, &Context::new(feature))?;
    println!(
        "{}",
        serde_json::to_string_pretty(
            &serde_json::json!({"feature":feature,"count":report.count(),"bytes":report.bytes(),"elapsed_ms":report.elapsed_ms,"applications_with_icons":report.apps.iter().filter(|a|a.icon.is_some()).count(),"applications_without_icons":report.apps.iter().filter(|a|a.icon.is_none()).map(|a|&a.name).collect::<Vec<_>>(),"categories":report.categories.iter().map(|c|serde_json::json!({"id":c.id,"count":c.count,"bytes":c.bytes})).collect::<Vec<_>>(),"diagnostics":report.diagnostics})
        )?
    );
    Ok(())
}
