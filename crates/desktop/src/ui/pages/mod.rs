mod applications;
mod cleanup;
mod duplicates;
pub use duplicates::expansion_ids as duplicate_expansion_ids;
mod files;
mod home;
mod popup;
mod system;
use super::{theme, widgets::*};
use crate::app::{Action, App, Dialog, Message};
use cplus_core::Feature;
use iced::{
    Element, Length,
    widget::{Space, column, container, progress_bar, row, scrollable},
};
pub fn subtitle(f: Feature) -> &'static str {
    match f {
        Feature::Home => "Disk space at a glance",
        Feature::Drive => "Organize by type and reclaim disk space",
        Feature::Chat => "Clean caches from your chat applications",
        Feature::LargeFiles => "Move large files to another drive",
        Feature::AppMover => "Move installed applications to another drive",
        Feature::Compression => "Save disk space with transparent compression",
        Feature::Duplicates => "Find identical files and keep one copy",
        Feature::Privacy => "Delete usage traces by category",
        Feature::Uninstall => "Manage installed applications",
        Feature::Popup => "Automatically block unwanted windows",
        Feature::System => "Manage Windows files and reclaim space as needed",
    }
}
pub fn view(app: &App) -> Element<'_, Message> {
    let content = match app.page {
        Feature::Home => home::view(app),
        Feature::Drive | Feature::Chat | Feature::Privacy => cleanup::view(app),
        Feature::AppMover | Feature::Compression | Feature::Uninstall => applications::view(app),
        Feature::LargeFiles => files::view(app),
        Feature::Duplicates => duplicates::view(app),
        Feature::Popup => popup::view(app),
        Feature::System => system::view(app),
    };
    column![content, progress(app)]
        .spacing(12)
        .height(Length::Fill)
        .into()
}
pub fn action(f: Feature) -> (Action, &'static str) {
    match f {
        Feature::AppMover => (Action::Move, "Move"),
        Feature::LargeFiles => (Action::Move, "Move files"),
        Feature::Compression => (Action::Compress, "Compress"),
        Feature::Uninstall => (Action::Uninstall, "Uninstall selected"),
        _ => (Action::Delete, "Delete"),
    }
}
pub fn main_button(
    app: &App,
    key: &str,
    action: Option<Message>,
    primary: bool,
) -> Element<'static, Message> {
    // Match the existing four-character Chinese action label; leave enough
    // room for English labels while keeping each scan/action pair identical.
    let width = if app.i18n.language == crate::i18n::Language::English {
        match app.page {
            Feature::Home => 144,
            Feature::Uninstall => 168,
            _ => 120,
        }
    } else {
        104
    };
    action_btn(app.t(key), action, primary, width)
}
pub fn hero(app: &App) -> Element<'static, Message> {
    let f = app.page;
    let compact = cleanup::compact(f);
    let report = app.reports.get(&f);
    let count = report.map(|r| r.count()).unwrap_or(0);
    let size = report.map(|r| r.bytes()).unwrap_or(0);
    let chosen = app.choices(f);
    let selected = if f == Feature::Privacy {
        report
            .map(|r| {
                r.categories
                    .iter()
                    .filter(|c| chosen.contains(&c.id))
                    .map(|c| c.count)
                    .sum()
            })
            .unwrap_or(0)
    } else {
        chosen.len() as u64
    };
    let apps = matches!(
        f,
        Feature::AppMover | Feature::Compression | Feature::Uninstall
    );
    let metric = if f.byte_metric() && !apps {
        bytes(size)
    } else {
        format!("{count} {}", app.t(if apps { "apps" } else { "entries" }))
    };
    let selected_metric = if apps {
        format!("{selected} {}", app.t("apps"))
    } else if matches!(f, Feature::Privacy) {
        format!("{selected} {}", app.t("entries"))
    } else {
        bytes(app.selected_size(f))
    };
    let (action, name) = action(f);
    let mut summary = column![
        row![
            bitmap("status-warning.png", if compact { 24 } else { 29 }),
            bold(
                app.t(if count > 0 {
                    "Found"
                } else if report.is_some_and(|r| r.cancelled) {
                    "Stopped"
                } else if report.is_some_and(|r| !r.token.is_empty()) {
                    "Scan complete"
                } else {
                    "Ready to scan"
                }),
                if compact { 17 } else { 19 }
            ),
            amount(metric, if compact { 26 } else { 30 })
        ]
        .spacing(if compact { 8 } else { 12 })
        .align_y(iced::Alignment::Center),
        row![small(app.t("Selected")), amount(selected_metric, 16)].spacing(9)
    ]
    .spacing(if compact { 6 } else { 13 })
    .width(Length::Fill);
    if apps {
        summary = summary.push(
            row![
                small(app.t(if f == Feature::AppMover {
                    "Software size"
                } else {
                    "Total"
                })),
                amount(bytes(size), 15),
                small(app.t("Selected")),
                amount(bytes(app.selected_size(f)), 15)
            ]
            .spacing(8),
        );
    }
    let disk = app.drive().map(|d| {
        column![
            small(format!("{}: {}", app.drive_letter(), app.t("Available"))),
            bold(bytes(d.free), 20).color(theme::MINT),
            progress_bar(0.0..=1.0, (d.used as f64 / d.total.max(1) as f64) as f32)
                .girth(8)
                .style(theme::capacity)
        ]
        .spacing(6)
        .width(if compact { 128 } else { 170 })
    });
    let mut r = row![summary]
        .spacing(if compact { 16 } else { 24 })
        .align_y(iced::Alignment::Center);
    if let Some(disk) = disk {
        r = r.push(disk);
    }
    r = r
        .push(main_button(
            app,
            "Scan",
            (!app.busy()).then_some(Message::Scan),
            false,
        ))
        .push(main_button(
            app,
            name,
            (!app.busy() && !chosen.is_empty() && report.is_some_and(|r| !r.token.is_empty()))
                .then_some(Message::Request(action, None)),
            true,
        ));
    container(r)
        .padding(if compact { [14, 18] } else { [24, 24] })
        .style(theme::hero)
        .into()
}
pub fn progress(app: &App) -> Element<'static, Message> {
    progress_for(app, app.page)
}
pub fn progress_for(app: &App, feature: Feature) -> Element<'static, Message> {
    if app
        .job
        .as_ref()
        .is_some_and(|job| job.name == "Loading history")
        || app
            .windows
            .values()
            .any(|dialog| *dialog == Dialog::History(feature))
    {
        return Space::new().height(0).into();
    }
    if let Some(job) = &app.job {
        let p = &app.progress;
        let ratio = p
            .total
            .filter(|n| *n > 0)
            .map(|n| (p.done as f64 / n as f64).min(1.0) as f32);
        let preparing = p.feature == Feature::Drive && p.phase == "Preparing scan locations";
        let detail = if preparing {
            app.t("Preparing scan locations")
        } else if let Some(matched) = p.matched {
            format!(
                "{} {} · {} {} · {} · {}s",
                app.t("Checked"),
                p.done,
                app.t("Found"),
                matched,
                bytes(p.bytes),
                p.elapsed_ms / 1000
            )
        } else {
            format!("{} · {} · {}s", p.done, bytes(p.bytes), p.elapsed_ms / 1000)
        };
        let mut top = row![
            small(format!(
                "{} · {}",
                app.name(p.feature),
                app.t(
                    if p.feature == Feature::Duplicates && job.name == "Scanning" {
                        if p.phase == "scan" {
                            "Enumerating files"
                        } else {
                            &p.phase
                        }
                    } else {
                        &job.name
                    }
                )
            )),
            Space::new().width(Length::Fill),
            small(detail)
        ]
        .spacing(10);
        if let Some(r) = ratio {
            top = top.push(amount(format!("{:.0}%", r * 100.0), 13));
        }
        if job.name == "Scanning"
            && (matches!(job.ctx.feature, Feature::LargeFiles | Feature::Duplicates)
                || p.feature == Feature::LargeFiles)
        {
            top = top.push(
                iced::widget::button(label(app.t("Stop")).size(12))
                    .padding([2, 8])
                    .style(theme::secondary)
                    .on_press_maybe(
                        (!job.ctx.cancel.cancelled()).then_some(Message::CancelLargeScan),
                    ),
            );
        }
        card(
            column![
                top,
                progress_bar(
                    0.0..=1.0,
                    ratio.unwrap_or((p.elapsed_ms % 1800) as f32 / 1800.0)
                )
                .girth(7)
                .style(theme::progress),
                small(if p.matched.is_some() {
                    format!(
                        "{}: {}",
                        app.t("Currently scanning"),
                        short(&cplus_core::common::display_text(&p.current), 105)
                    )
                } else {
                    short(&cplus_core::common::display_text(&p.current), 95)
                })
            ]
            .spacing(7),
        )
    } else if !app.status.is_empty() && app.status_feature == feature {
        let mut r = row![small(&app.status)].spacing(12);
        if let Some(elapsed) = app.status_elapsed {
            r = r.push(small(elapsed_text(app, elapsed)));
        }
        if feature == Feature::Duplicates {
            r = r.push(small(duplicates::statistics(app)).width(Length::Fill));
        }
        if feature != Feature::Duplicates {
            r = r.push(Space::new().width(Length::Fill));
        }
        if !app.issues.is_empty() {
            r = r.push(link(
                format!("{} {}", app.issues.len(), app.t("Issues")),
                Message::Dialog(Dialog::Information),
            ));
        }
        r.into()
    } else if app.scan_elapsed.contains_key(&feature) {
        small(if feature == Feature::Duplicates {
            format!(
                "{} · {}",
                scan_complete_text(app, feature),
                duplicates::statistics(app)
            )
        } else {
            scan_complete_text(app, feature)
        })
        .into()
    } else {
        Space::new().height(0).into()
    }
}
pub fn elapsed_text(app: &App, elapsed_ms: u64) -> String {
    format!(
        "{} {:.1} {}",
        app.t("Time taken"),
        elapsed_ms as f64 / 1000.0,
        app.t("seconds")
    )
}
pub fn scan_complete_text(app: &App, feature: Feature) -> String {
    match app.scan_elapsed.get(&feature) {
        Some(elapsed) => format!(
            "{} · {}",
            app.t("Scan complete"),
            elapsed_text(app, *elapsed)
        ),
        None => app.t("Scan complete"),
    }
}
pub fn disk_card(app: &App, total: bool) -> Element<'static, Message> {
    let disks = if total {
        app.disks.iter().collect::<Vec<_>>()
    } else {
        app.drive().into_iter().collect()
    };
    let capacity = disks.iter().map(|d| d.total).sum::<u64>();
    let used = disks.iter().map(|d| d.used).sum::<u64>();
    let free = capacity.saturating_sub(used);
    card(
        column![
            row![
                bold(
                    if total {
                        app.t("All drives")
                    } else {
                        app.drive_name()
                    },
                    15
                ),
                Space::new().width(Length::Fill),
                small(format!(
                    "{} {} / {} {}",
                    app.t("Used"),
                    bytes(used),
                    app.t("Total"),
                    bytes(capacity)
                ))
            ],
            progress_bar(0.0..=1.0, (used as f64 / capacity.max(1) as f64) as f32)
                .girth(9)
                .style(theme::capacity),
            row![
                Space::new().width(Length::Fill),
                small(app.t("Available")),
                bold(bytes(free), 15).color(theme::MINT)
            ]
            .spacing(8)
        ]
        .spacing(10),
    )
}
pub fn toolbar(app: &App) -> Element<'static, Message> {
    row![
        link(app.t("Select all"), Message::All(true)),
        link(app.t("Select none"), Message::All(false)),
        Space::new().width(Length::Fill)
    ]
    .spacing(8)
    .into()
}
pub fn footer(app: &App, f: Feature) -> Element<'static, Message> {
    let path = if f == Feature::AppMover {
        &app.destination_apps
    } else {
        &app.destination_files
    };
    container(
        row![
            small(app.t(if f == Feature::LargeFiles {
                "Move files to"
            } else {
                "Destination"
            })),
            label(short(&cplus_core::common::display_path(path), 58)),
            Space::new().width(Length::Fill),
            btn(
                app.t("Choose folder"),
                (!app.busy()).then_some(Message::Folder(f)),
                false
            ),
            link(
                app.t(if f == Feature::AppMover {
                    "Move History"
                } else {
                    "Large File History"
                }),
                Message::Dialog(Dialog::History(f))
            )
        ]
        .spacing(10)
        .align_y(iced::Alignment::Center),
    )
    .padding(15)
    .style(theme::hero)
    .into()
}
pub fn content_scroll<'a>(body: impl Into<Element<'a, Message>>) -> Element<'a, Message> {
    scrollable(body)
        .spacing(10)
        .height(Length::Fill)
        .width(Length::Fill)
        .into()
}
