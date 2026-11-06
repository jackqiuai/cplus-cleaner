use crate::{jobs::Context, *};
use anyhow::Result;

pub fn scan_feature(
    feature: Feature,
    store: &Store,
    folder: Option<&std::path::Path>,
    days: u32,
    ctx: &Context,
) -> Result<ScanResult> {
    let started = std::time::Instant::now();
    let mut report = match feature {
        Feature::Drive => super::drive_cleanup::scan(days, ctx),
        Feature::Chat => super::chat_cleanup::scan(store, ctx),
        Feature::LargeFiles => super::large_files::scan(ctx),
        Feature::AppMover => super::app_mover::scan(ctx),
        Feature::Compression => super::app_compression::scan(store, ctx),
        Feature::Privacy => super::privacy::scan(ctx),
        Feature::Uninstall => super::uninstall::scan(ctx),
        Feature::Popup => super::popup_blocker::scan(store, ctx),
        Feature::System => super::system_slim::scan(ctx),
        Feature::Duplicates => super::duplicates::scan(
            folder.ok_or_else(|| anyhow::anyhow!("Choose a folder to scan"))?,
            ctx,
        ),
        Feature::Home => anyhow::bail!("Home scanning requires per-feature results"),
    }?;
    report.elapsed_ms = started.elapsed().as_millis() as u64;
    Ok(report)
}
fn scan_home_feature(
    feature: Feature,
    store: &Store,
    locations: &[super::duplicates::Location],
    minimum: u64,
    ctx: &Context,
) -> Result<ScanResult> {
    if feature == Feature::Duplicates {
        super::duplicates::scan_locations(locations, minimum, ctx)
    } else {
        scan_feature(feature, store, None, 0, ctx)
    }
}

pub fn scan(
    store: &Store,
    pending: &[Feature],
    locations: &[super::duplicates::Location],
    minimum: u64,
    ctx: &Context,
    mut completed: impl FnMut(ScanResult),
) -> Vec<Issue> {
    let mut errors = vec![];
    for &feature in pending {
        if ctx.cancel.cancelled() {
            break;
        }
        let child = ctx.child(feature);
        match scan_home_feature(feature, store, locations, minimum, &child) {
            Ok(report) => completed(report),
            Err(error) => errors.push(Issue {
                path: feature.key().into(),
                message: error.to_string(),
            }),
        }
    }
    errors
}
pub fn delete(
    store: &Store,
    report: &ScanResult,
    selected: &[String],
    ctx: &Context,
) -> OperationResult {
    let mut result = match report.feature {
        Feature::LargeFiles => return super::large_files::delete(store, report, selected, ctx),
        Feature::Privacy => return super::privacy::delete(store, report, selected, ctx),
        Feature::Chat => {
            return super::chat_cleanup::delete(
                store,
                report,
                &selected.iter().cloned().collect(),
                ctx,
            );
        }
        Feature::Duplicates => {
            super::duplicates::delete(store, report, &selected.iter().cloned().collect(), ctx)
        }
        _ => {
            let entries = report
                .files
                .iter()
                .filter(|f| selected.contains(&f.id) || selected.contains(&f.category))
                .cloned()
                .collect::<Vec<_>>();
            crate::common::delete_entries(&entries, &report.rules, ctx)
        }
    };
    if report.feature == Feature::Drive {
        super::drive_cleanup::delete_windows(store, report, selected, ctx, &mut result);
    }
    let _ = store.append(
        &format!("{}_delete", report.feature.key()),
        "delete",
        result.count,
        result.bytes,
        result.ok(),
    );
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::features::duplicates::{DEFAULT_MINIMUM, Location};
    #[test]
    fn last_home_tool_scans_duplicates_using_the_selected_scope_and_minimum() {
        let temp = tempfile::tempdir().unwrap();
        let selected = temp.path().join("selected");
        let outside = temp.path().join("outside");
        std::fs::create_dir(&selected).unwrap();
        std::fs::create_dir(&outside).unwrap();
        for root in [&selected, &outside] {
            for name in ["a.zip", "b.zip"] {
                std::fs::write(root.join(name), vec![3; DEFAULT_MINIMUM as usize + 1]).unwrap();
            }
            for name in ["a.txt", "b.txt"] {
                std::fs::write(root.join(name), b"small duplicate").unwrap();
            }
        }
        let store = Store::new(Some(temp.path().join("profile")), true).unwrap();
        let feature = *Feature::HOME.last().unwrap();
        let ctx = Context::new(Feature::Home).child(feature);
        let mut reports = vec![];
        let errors = scan(
            &store,
            &[feature],
            &[Location::cleanable(&selected)],
            DEFAULT_MINIMUM,
            &ctx,
            |report| reports.push(report),
        );
        assert!(errors.is_empty());
        assert_eq!(reports.len(), 1);
        let report = reports.pop().unwrap();
        assert_eq!(report.feature, Feature::Duplicates);
        assert_eq!(report.files.len(), 2);
        assert_eq!(report.categories.len(), 1);
        assert!(
            report
                .files
                .iter()
                .all(|f| crate::common::inside(&f.path, &selected) && f.size > DEFAULT_MINIMUM)
        );
        let summary = report.duplicate_scan.unwrap();
        assert_eq!(summary.minimum, DEFAULT_MINIMUM);
        assert_eq!(summary.locations.len(), 1);
        assert_eq!(
            crate::common::key(&summary.locations[0].path),
            crate::common::key(&selected)
        );
        assert!(!summary.locations[0].protected);
    }
}
