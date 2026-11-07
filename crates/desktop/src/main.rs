#![windows_subsystem = "windows"]
mod app;
mod i18n;
mod integrations;
mod monitor;
mod ui;

fn main() -> anyhow::Result<()> {
    if cplus_platform_windows::monitor::cpu_service::command()? {
        return Ok(());
    }
    let config = app::Config::parse()?;
    if let Some(path) = config.smoke.as_ref() {
        let report = cplus_core::validation::bundle_report()?;
        cplus_core::storage::atomic_write(path, &serde_json::to_vec_pretty(&report)?)?;
        return Ok(());
    }
    if let Some(path) = config.system_job.as_ref() {
        return cplus_core::features::system_slim::elevated_job(path);
    }
    if config.scheduled {
        let store = cplus_core::Store::new(None, false)?;
        let lock = cplus_platform_windows::processes::SingleInstance::acquire(
            "CPlusCleaner-Scheduled-Rust",
            false,
        )?;
        if !lock.primary {
            return Ok(());
        }
        let ctx = cplus_core::jobs::Context::new(cplus_core::Feature::Drive);
        let report = cplus_core::features::drive_cleanup::scan(0, &ctx)?;
        let selected = report
            .categories
            .iter()
            .filter(|c| {
                c.default_selected
                    && !matches!(c.id.as_str(), "drive:windows_old" | "drive:windows_updates")
            })
            .map(|c| c.id.clone())
            .collect::<Vec<_>>();
        let result = cplus_core::features::home::delete(&store, &report, &selected, &ctx);
        if !result.ok() {
            anyhow::bail!(
                "Scheduled cleanup completed with {} skipped items",
                result.errors.len()
            );
        }
        return Ok(());
    }
    let lock = cplus_platform_windows::processes::SingleInstance::acquire(
        if config.preview {
            "CPlusCleaner-Rust-Preview"
        } else {
            "CPlusCleaner-Rust"
        },
        true,
    )?;
    if !lock.primary {
        return Ok(());
    }
    let mut application = iced::daemon(
        move || app::App::new(config.clone()),
        app::App::update,
        app::App::view,
    )
    .title(app::App::title)
    .theme(|_: &app::App, _: iced::window::Id| ui::theme::application())
    .subscription(app::App::subscription)
    .default_font(ui::fonts::regular());
    for face in ui::fonts::system_faces() {
        application = application.font(face);
    }
    application.run()?;
    drop(lock);
    Ok(())
}
