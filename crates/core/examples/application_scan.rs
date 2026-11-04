//! Read-only scan diagnostics. Pass an isolated profile when refreshing history.
use cplus_core::{
    features::{app_compression, app_mover, uninstall},
    jobs::Context,
    *,
};
use std::{path::PathBuf, time::Instant};
fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("catalog") => {
            let apps = app_mover::candidates(&Context::new(Feature::AppMover));
            println!("{}", serde_json::to_string_pretty(&apps)?);
        }
        Some("measure") => {
            let mut rows = vec![];
            for root in args.map(PathBuf::from) {
                let started = Instant::now();
                let measured = uninstall::measure_for_scan(&root, &Context::new(Feature::AppMover));
                rows.push(serde_json::json!({"root":root,"elapsed_ms":started.elapsed().as_millis(),"logical":measured.logical,"physical":measured.physical,"files":measured.files.len(),"modified":measured.modified,"missing":measured.missing,"unreadable":measured.unreadable,"linked":measured.linked,"compressed":measured.compressed,"batch_pages":measured.stats.batch_pages,"fallback":measured.stats.fallback}));
            }
            println!("{}", serde_json::to_string_pretty(&rows)?);
        }
        Some("pages") => {
            let profile = args
                .next()
                .map(PathBuf::from)
                .ok_or_else(|| anyhow::anyhow!("An isolated profile is required"))?;
            let store = Store::new(Some(profile), true)?;
            let mut results = vec![];
            for feature in [Feature::AppMover, Feature::Compression] {
                let ctx = Context::new(feature);
                let report = if feature == Feature::AppMover {
                    app_mover::scan(&ctx)?
                } else {
                    app_compression::scan(&store, &ctx)?
                };
                results.push(serde_json::json!({"feature":feature,"elapsed_ms":report.elapsed_ms,"count":report.apps.len(),"counts":report.counts,"diagnostics":report.diagnostics,"apps":report.apps.iter().map(|a| serde_json::json!({"id":a.id,"name":a.name,"path":a.path,"logical":a.logical,"physical":a.physical,"files":a.files,"modified":a.modified,"blocked":a.blocked})).collect::<Vec<_>>()}));
            }
            println!("{}", serde_json::to_string_pretty(&results)?);
        }
        _ => anyhow::bail!("Use catalog, measure <roots...>, or pages <isolated-profile>"),
    }
    Ok(())
}
