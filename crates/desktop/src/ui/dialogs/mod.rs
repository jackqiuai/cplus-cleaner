use super::{pages, theme, widgets::*};
mod activity;
mod feedback;
mod settings;
use crate::app::Pending;
use crate::{
    app::{Action, App, Dialog, Message},
    i18n::Language,
};
use cplus_core::Feature;
pub(crate) use feedback::environment;
use iced::{
    Element, Length,
    widget::{Column, Space, column, container, row, scrollable},
    window,
};

pub fn view<'a>(app: &'a App, id: window::Id, dialog: &Dialog) -> Element<'a, Message> {
    if matches!(dialog, Dialog::Confirm | Dialog::Information) {
        return prompt(app, id, *dialog == Dialog::Confirm);
    }
    if *dialog == Dialog::Updates {
        return updates(app, id);
    }
    if *dialog == Dialog::Settings {
        return settings::view(app);
    }
    if *dialog == Dialog::Activity {
        return activity::view(app);
    }
    if *dialog == Dialog::Feedback {
        return feedback::view(app, id);
    }
    let title = row![logo(33), bold(app.dialog_title(dialog), 25)]
        .spacing(14)
        .align_y(iced::Alignment::Center);
    let mut body = if *dialog == Dialog::About {
        Column::new()
    } else {
        column![title]
    }
    .spacing(if *dialog == Dialog::About { 12 } else { 23 });
    let content: Element<'a, Message> = match dialog {
        Dialog::Settings => unreachable!(),
        Dialog::Activity => unreachable!(),
        Dialog::Feedback => unreachable!(),
        Dialog::Donate => donate(app),
        Dialog::About => about(app),
        Dialog::Updates => unreachable!(),
        Dialog::History(f) => history(app, *f),
        Dialog::Residue => residue(app),
        Dialog::Confirm | Dialog::Information => unreachable!(),
    };
    body = body.push(content);
    let mut footer = row![Space::new().width(Length::Fill)].spacing(12);
    match dialog {
        Dialog::Residue => {
            footer = footer
                .push(btn(
                    app.t("Delete selected"),
                    (!app.busy() && !app.residue_selected.is_empty())
                        .then_some(Message::DeleteResidues),
                    true,
                ))
                .push(btn(app.t("Close"), Some(Message::Dismiss(id)), false))
        }
        _ => footer = footer.push(btn(app.t("Close"), Some(Message::Dismiss(id)), false)),
    }
    if matches!(dialog, Dialog::Residue) {
        body = body.push(pages::progress_for(app, app.progress.feature));
    }
    body = body.push(footer);
    container(body)
        .padding(if *dialog == Dialog::About { 20 } else { 26 })
        .width(Length::Fill)
        .height(Length::Fill)
        .style(theme::surface)
        .into()
}
pub(crate) fn prompt_size(app: &App, confirmation: bool) -> iced::Size {
    if confirmation {
        let detailed = app.pending.as_ref().is_some_and(|p| {
            p.ids.len() > 3
                || matches!(p.action, Action::Compress | Action::System(_))
                || p.ids.iter().any(|id| id == "drive:windows_old")
        });
        iced::Size::new(400.0, if detailed { 360.0 } else { 280.0 })
    } else {
        let detailed = app.info.chars().count() > 90
            || app.info.lines().count() > 2
            || app.issues.len() > 1
            || app
                .issues
                .iter()
                .any(|i| i.path.chars().count() > 80 || i.message.chars().count() > 90);
        iced::Size::new(360.0, if detailed { 360.0 } else { 220.0 })
    }
}
fn pending_capacity(p: &Pending, report: &cplus_core::ScanResult) -> Option<u64> {
    if p.ids.is_empty() || matches!(p.action, Action::Restore(_) | Action::System(_)) {
        return None;
    }
    Some(if p.feature == Feature::Drive {
        report
            .categories
            .iter()
            .filter(|c| p.ids.contains(&c.id))
            .map(|c| c.bytes)
            .sum()
    } else if !report.apps.is_empty() {
        report
            .apps
            .iter()
            .filter(|a| p.ids.contains(&a.id))
            .map(|a| {
                if p.feature == Feature::Compression {
                    a.physical
                } else {
                    a.logical
                }
            })
            .sum()
    } else {
        report
            .files
            .iter()
            .filter(|f| p.ids.contains(&f.id) || p.ids.contains(&f.category))
            .map(|f| {
                if p.feature == Feature::Duplicates {
                    f.allocated
                } else {
                    f.size
                }
            })
            .sum()
    })
}
fn prompt(app: &App, id: window::Id, confirmation: bool) -> Element<'_, Message> {
    let deletion = confirmation
        && app
            .pending
            .as_ref()
            .is_some_and(|p| matches!(p.action, Action::Delete));
    let icon = iced::widget::svg(iced::widget::svg::Handle::from_memory(if confirmation {
        br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#fff6e7"/><path d="m16 6 11 19H5Z" fill="none" stroke="#dba124" stroke-width="1.7" stroke-linejoin="round"/><path d="M16 12v6m0 3h.01" stroke="#dba124" stroke-width="2" stroke-linecap="round"/></svg>"##.as_slice()
    } else {
        br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#e8faf2"/><circle cx="16" cy="16" r="10" fill="none" stroke="#0bb47e" stroke-width="1.7"/><path d="M16 15v7m0-12h.01" stroke="#0bb47e" stroke-width="2" stroke-linecap="round"/></svg>"##.as_slice()
    })).width(32).height(32);
    let heading = row![
        icon,
        bold(
            app.t(if deletion {
                "Confirm deletion of selected items?"
            } else if confirmation {
                "Confirm operation"
            } else {
                "Notice"
            }),
            17
        )
    ]
    .spacing(10)
    .align_y(iced::Alignment::Center);
    let button = |text: String, message: Option<Message>, primary: bool| {
        iced::widget::button(
            container(bold(text, 13).color(if primary {
                iced::Color::WHITE
            } else {
                theme::MINT
            }))
            .center_x(Length::Fill)
            .center_y(Length::Fill),
        )
        .width(if primary { 112 } else { 86 })
        .height(34)
        .padding(0)
        .on_press_maybe(message)
        .style(move |theme, status| {
            let mut style = if primary {
                theme::primary(theme, status)
            } else {
                theme::secondary(theme, status)
            };
            style.border.radius = 6.0.into();
            if primary && deletion && !matches!(status, iced::widget::button::Status::Disabled) {
                style.background = Some(theme::CORAL.into());
            }
            style
        })
    };
    let footer: Element<'_, Message> = if confirmation {
        row![
            Space::new().width(Length::Fill),
            button(app.t("Cancel"), Some(Message::Dismiss(id)), false),
            button(
                app.t(if deletion {
                    "Confirm deletion"
                } else {
                    "Confirm"
                }),
                (!app.busy() && app.pending.is_some()).then_some(Message::Confirm),
                true
            )
        ]
        .spacing(10)
        .into()
    } else {
        container(button(app.t("OK"), Some(Message::Dismiss(id)), true))
            .center_x(Length::Fill)
            .into()
    };
    let content = if confirmation {
        confirm(app)
    } else {
        information(app)
    };
    container(column![heading, content, footer].spacing(12))
        .padding(18)
        .width(Length::Fill)
        .height(Length::Fill)
        .style(|_| container::Style {
            background: Some(iced::Color::WHITE.into()),
            ..Default::default()
        })
        .into()
}
fn donate(app: &App) -> Element<'_, Message> {
    if app.i18n.language == Language::English {
        column![
            logo(64),
            bold("CPlus Cleaner", 22),
            label(app.t("Voluntary donations help maintain this free project.")),
            btn(
                "Buy me a coffee",
                Some(Message::Open("https://ko-fi.com/qiujack".into())),
                true
            ),
            Space::new().height(Length::Fill)
        ]
        .spacing(25)
        .align_x(iced::Alignment::Center)
        .into()
    } else {
        column![
            small(app.t("Voluntary donations help maintain this free project.")),
            container(bitmap("donation/wechat.png", 370)).center_x(Length::Fill),
            bold(app.t("Scan with WeChat"), 17),
            Space::new().height(Length::Fill)
        ]
        .spacing(15)
        .align_x(iced::Alignment::Center)
        .into()
    }
}
fn about(app: &App) -> Element<'_, Message> {
    column![
        logo(72),
        bold("CPlus Cleaner", 25),
        small(format!("V{} · Windows x64", cplus_core::VERSION)),
        label(app.t("A free, portable Windows drive cleanup assistant.")),
        small(app.t("If this app helps you, a GitHub star is appreciated.")),
        iced::widget::button(
            row![
                iced::widget::svg(iced::widget::svg::Handle::from_memory(
                    br##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#f2cc60" d="m12 2 3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.76 5.82 21 7 14.14 2 9.27l6.91-1.01Z"/></svg>"##
                )).width(17).height(17),
                bold(app.t("Star this project"), 14).color(iced::Color::WHITE)
            ].spacing(7).align_y(iced::Alignment::Center)
        ).padding([11, 18]).style(theme::primary).on_press(Message::Open(
                "https://github.com/jackqiuai/cplus-cleaner".into()
            )),
        small(app.t("License: MIT")),
        iced::widget::button(small(app.t("Third-party licenses"))).padding(0).style(theme::menu_item).on_press(Message::CpuTemperatureLicenses),
        Space::new().height(Length::Fill)
    ]
    .spacing(8)
    .align_x(iced::Alignment::Center)
    .into()
}
fn updates(app: &App, id: window::Id) -> Element<'_, Message> {
    let failed = app.release.is_none() && !app.update_error.is_empty();
    let title = if let Some(release) = &app.release {
        if release.newer {
            "A newer version is available"
        } else {
            "You are up to date"
        }
    } else if failed {
        "Unable to check for updates"
    } else {
        "Checking the latest stable release…"
    };
    let mark = if failed {
        "M12 7v6m0 4h.01"
    } else if app.release.as_ref().is_some_and(|r| r.newer) {
        "M7 12l5-5 5 5m-5-5v11"
    } else if app.release.is_some() {
        "M6 12l4 4 8-8"
    } else {
        "M7 12h.01M12 12h.01M17 12h.01"
    };
    let color = if failed { "#ff5966" } else { "#16c88d" };
    let icon = format!(
        r##"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><circle cx="48" cy="48" r="46" fill="{color}" opacity=".05"/><circle cx="48" cy="48" r="37" fill="{color}" opacity=".12"/><circle cx="48" cy="48" r="28" fill="{color}"/><g transform="translate(24 24) scale(2)" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="{mark}"/></g></svg>"##
    );
    let versions = container(
        column![
            row![
                small(app.t("Current version"))
                    .color(theme::INK)
                    .width(Length::Fill),
                label(format!("v{}", cplus_core::VERSION)).size(12)
            ],
            iced::widget::rule::horizontal(1),
            row![
                small(app.t("Latest release"))
                    .color(theme::INK)
                    .width(Length::Fill),
                label(
                    app.release
                        .as_ref()
                        .map(|r| r.tag.clone())
                        .unwrap_or_else(|| "--".into())
                )
                .size(12)
            ]
        ]
        .spacing(8),
    )
    .padding([10, 20])
    .width(256)
    .style(|_| container::Style {
        background: Some(theme::PALE.into()),
        border: iced::Border {
            radius: 6.0.into(),
            ..Default::default()
        },
        ..Default::default()
    });
    container(
        column![
            iced::widget::svg(iced::widget::svg::Handle::from_memory(icon.into_bytes()))
                .width(84)
                .height(84),
            bold(app.t(title), 17).align_x(iced::Alignment::Center),
            small(if failed {
                short(&app.t(&app.update_error), 85)
            } else {
                app.t("Thank you for using CPlus Cleaner")
            })
            .size(11)
            .align_x(iced::Alignment::Center),
            versions,
            small(app.t("Version information comes from GitHub Releases")).size(10),
            iced::widget::button(
                container(bold(app.t("OK"), 13).color(iced::Color::WHITE))
                    .center_x(Length::Fill)
                    .center_y(Length::Fill)
            )
            .padding(0)
            .width(136)
            .height(32)
            .style(theme::primary)
            .on_press(Message::Dismiss(id))
        ]
        .spacing(6)
        .align_x(iced::Alignment::Center),
    )
    .padding(10)
    .center_x(Length::Fill)
    .center_y(Length::Fill)
    .style(|_| container::Style {
        background: Some(iced::Color::WHITE.into()),
        ..Default::default()
    })
    .into()
}
fn history(app: &App, feature: Feature) -> Element<'_, Message> {
    let mut rows = Column::new().spacing(12);
    if app.history.get(&feature).is_none_or(Vec::is_empty) {
        rows = rows.push(empty(app.t("No records")));
    }
    for row in app.history.get(&feature).into_iter().flatten() {
        let name = row["name"]
            .as_str()
            .or(row["software"].as_str())
            .or(row["path"].as_str())
            .unwrap_or("CPlus Cleaner");
        let id = row["id"].as_str().unwrap_or("");
        let date = display_time(&row["time"]);
        let mut heading = row![
            bold(short(name, 70), 16),
            small(date),
            Space::new().width(Length::Fill)
        ]
        .spacing(13)
        .align_y(iced::Alignment::Center);
        let restored = row["restored"] == true || row["superseded"] == true;
        if matches!(feature, Feature::Compression | Feature::AppMover) {
            let can_restore = row["refresh_error"].as_str().is_none_or(str::is_empty)
                && !id.is_empty()
                && !restored
                && !app.busy()
                && (feature == Feature::Compression || row["ok"] == true);
            heading = heading.push(btn(
                app.t(if restored {
                    "Restored"
                } else if row["operation"] == "restore" && row["status"] != "success" {
                    "Continue restore"
                } else {
                    "Restore"
                }),
                can_restore.then_some(Message::HistoryRestore(feature, id.into())),
                false,
            ));
        }
        let mut content = column![heading].spacing(11);
        if feature == Feature::Compression {
            let before = row["old_size"].as_u64();
            let after = row["new_size"].as_u64();
            let current = row["current_size"].as_u64();
            let value = if let (Some(before), Some(after), true) = (
                before,
                after,
                row["schema"].as_u64().is_some_and(|v| v >= 2),
            ) {
                format!(
                    "{} {}  ·  {} {}  ·  {} {}",
                    app.t("Before"),
                    bytes(before),
                    app.t("After"),
                    bytes(after),
                    app.t("Saved"),
                    bytes(before.saturating_sub(after))
                )
            } else {
                format!(
                    "{} {}",
                    app.t("Current"),
                    current.map(bytes).unwrap_or_else(|| "—".into())
                )
            };
            content = content.push(small(value));
            let errors = row["last_errors"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_str())
                .map(|s| app.t(s))
                .collect::<Vec<_>>();
            if !errors.is_empty() && !restored {
                content = content.push(
                    small(format!(
                        "{}: {}",
                        app.t("Incomplete reason"),
                        one_line(&errors.join(" · "))
                    ))
                    .color(theme::CORAL),
                );
            }
        } else if feature == Feature::Popup {
            content = content.push(label(app.t(row["popup"].as_str().unwrap_or(""))));
        } else {
            let source = row["source"]
                .as_str()
                .or(row["path"].as_str())
                .unwrap_or("");
            let target = row["target"].as_str().unwrap_or("");
            content = content.push(
                row![
                    amount(bytes(row["size"].as_u64().unwrap_or(0)), 16),
                    small(app.t(match row["status"].as_str().unwrap_or("") {
                        "success" => "Success",
                        "cancelled" => "Stopped",
                        "copying" => "Working",
                        "restored" => "Restored",
                        "backup_retained" => "Partial completion",
                        _ =>
                            if row["ok"] == true {
                                "Success"
                            } else {
                                "Operation failed"
                            },
                    }))
                ]
                .spacing(12),
            );
            content = content
                .push(small(format!(
                    "{}: {}",
                    app.t("Original folder"),
                    short(source, 100)
                )))
                .push(small(format!(
                    "{}: {}",
                    app.t("Moved to"),
                    short(target, 100)
                )));
            if !target.is_empty() {
                content = content.push(
                    row![
                        link(app.t("Open"), Message::Reveal(target.into())),
                        link(
                            format!("{} · {}", app.t("Copy path"), app.t("Original folder")),
                            Message::Copy(source.into())
                        ),
                        link(
                            format!("{} · {}", app.t("Copy path"), app.t("Moved to")),
                            Message::Copy(target.into())
                        )
                    ]
                    .spacing(10),
                );
            }
            if let Some(error) = row["error"].as_str().filter(|s| !s.is_empty()) {
                content = content.push(
                    small(format!(
                        "{}: {}",
                        app.t("Incomplete reason"),
                        one_line(&app.t(error))
                    ))
                    .color(theme::CORAL),
                );
            }
        }
        if let Some(error) = row["refresh_error"].as_str().filter(|s| !s.is_empty()) {
            content = content.push(
                small(format!(
                    "{}: {}",
                    app.t("Incomplete reason"),
                    one_line(&app.t(error))
                ))
                .color(theme::CORAL),
            );
        }
        rows = rows.push(card(content));
    }
    scrollable(rows).height(Length::Fill).into()
}
fn residue(app: &App) -> Element<'_, Message> {
    let mut rows = Column::new().spacing(12);
    if let Some(report) = &app.residue {
        rows = rows.push(
            row![
                bold(app.t("Found"), 17),
                amount(
                    format!(
                        "{} {}",
                        report.files.len() + report.registry_residues.len(),
                        app.t("items")
                    ),
                    20
                ),
                Space::new().width(Length::Fill),
                small(app.t("Selected")),
                amount(
                    bytes(
                        report
                            .categories
                            .iter()
                            .filter(|c| app.residue_selected.contains(&c.id))
                            .map(|c| c.bytes)
                            .sum()
                    ),
                    18
                )
            ]
            .spacing(10),
        );
        for category in &report.categories {
            rows = rows.push(card(
                row![
                    check(
                        app.residue_selected.contains(&category.id),
                        (!app.busy() && !category.view_only)
                            .then_some(Message::Toggle(category.id.clone()))
                    ),
                    column![
                        bold(category.name.clone(), 16),
                        small(app.t(&category.group)),
                        small(short(&category.status, 100)),
                        small(if category.view_only {
                            app.t("View only")
                        } else {
                            String::new()
                        })
                    ]
                    .spacing(8)
                    .width(Length::Fill),
                    amount(bytes(category.bytes), 17)
                ]
                .spacing(14)
                .align_y(iced::Alignment::Center),
            ));
        }
    }
    if app.residue.as_ref().is_none_or(|r| r.categories.is_empty()) {
        rows = rows.push(empty(app.t("No matching items")));
    }
    scrollable(rows).height(Length::Fill).into()
}
fn confirm(app: &App) -> Element<'_, Message> {
    let Some(p) = &app.pending else {
        return empty(app.t("No matching items"));
    };
    if let Action::System(action) = p.action {
        use cplus_core::features::system_slim::Action as S;
        let title = match action {
            S::ClearDeliveryOptimization => "Windows Delivery Optimization files",
            S::ClearWindowsUpdate => "Windows Update cleanup",
            S::ClearPreviousWindows => "Previous Windows installations",
            S::CleanupComponents if p.feature == Feature::Drive => "Windows Update cleanup",
            S::CleanupComponents => "Clean components",
            S::EmptyRecycleBin => "Empty recycle bin",
            S::HibernateOff => "Disable hibernation",
            S::HibernateOn => "Enable hibernation",
            S::PagefileSettings => "Virtual memory settings",
            S::AnalyzeComponents => "Analyze components",
        };
        return scrollable(column![bold(app.t(title), 14), label(app.t("Windows may ask for administrator permission.")).size(13),
            small(app.t("Windows will perform this operation. Other selected cleanup items are not included."))
        ].spacing(8)).height(Length::Fill).into();
    }
    let text = match p.action {
        Action::Delete => "Files will be permanently deleted and cannot be recovered.",
        Action::System(_) => "Windows may ask for administrator permission.",
        _ => "Close the selected applications before continuing.",
    };
    let mut rows = column![small(app.name(p.feature)), label(app.t(text)).size(13)].spacing(8);
    if !p.ids.is_empty() {
        let mut summary = column![row![
            small(app.t("Selected")).width(Length::Fill),
            bold(format!("{} {}", p.ids.len(), app.t("items")), 13)
        ]]
        .spacing(8);
        if let Some(capacity) = app
            .reports
            .get(&p.feature)
            .and_then(|r| pending_capacity(p, r))
        {
            summary = summary.push(row![
                small(app.t("Selected size")).width(Length::Fill),
                amount(bytes(capacity), 14)
            ]);
        }
        rows = rows.push(
            container(summary)
                .padding([10, 12])
                .width(Length::Fill)
                .style(|_| container::Style {
                    background: Some(theme::PALE.into()),
                    border: iced::Border {
                        radius: 7.0.into(),
                        ..Default::default()
                    },
                    ..Default::default()
                }),
        );
    }
    if p.feature == Feature::Drive && p.ids.iter().any(|id| id == "drive:windows_old") {
        rows = rows.push(label(app.t("Deleting previous Windows installations prevents reverting to that Windows version.")).color(theme::CORAL));
    }
    if matches!(p.action, Action::Compress) {
        rows = rows.push(small(app.t(
            "Compression can slow application startup. Moving apps is preferred.",
        )));
    }
    if let Some(report) = app.reports.get(&p.feature) {
        if p.feature == Feature::LargeFiles {
            let files = report
                .files
                .iter()
                .filter(|file| p.ids.contains(&file.id))
                .collect::<Vec<_>>();
            rows = rows
                .push(small(format!(
                    "{} {}",
                    app.t("Size"),
                    bytes(files.iter().map(|file| file.size).sum())
                )))
                .push(small(format!(
                    "{} {}",
                    app.t("Estimated reclaimable"),
                    bytes(
                        files
                            .iter()
                            .filter(|file| file.blocked.is_empty() && file.identity.links == 1)
                            .map(|file| file.allocated)
                            .sum()
                    )
                )));
        }
        for id in p.ids.iter().take(16) {
            let name = report
                .apps
                .iter()
                .find(|a| a.id == *id)
                .map(|a| a.name.clone())
                .or_else(|| {
                    report
                        .categories
                        .iter()
                        .find(|c| c.id == *id)
                        .map(|c| app.t(&c.name))
                })
                .or_else(|| {
                    report
                        .files
                        .iter()
                        .find(|f| f.id == *id)
                        .map(|f| cplus_core::common::display_path(&f.path))
                })
                .unwrap_or_default();
            rows = rows.push(small(name));
        }
        if p.ids.len() > 16 {
            rows = rows.push(small(format!(
                "{}: {}",
                app.t("Additional items"),
                p.ids.len() - 16
            )));
        }
    }
    scrollable(rows).height(Length::Fill).into()
}
fn information(app: &App) -> Element<'_, Message> {
    let mut rows = Column::new().spacing(8);
    if !app.info.is_empty() {
        rows = rows.push(label(app.t(&app.info)).size(13));
    }
    for issue in app.issues.iter().take(50) {
        rows = rows.push(
            column![
                small(issue.path.clone()),
                label(app.t(&issue.message)).size(13)
            ]
            .spacing(5),
        );
    }
    scrollable(rows).height(Length::Fill).into()
}
fn display_time(v: &serde_json::Value) -> String {
    if let Some(s) = v.as_str() {
        s.into()
    } else {
        v.as_i64()
            .and_then(|n| chrono::DateTime::from_timestamp(n, 0))
            .map(|d| {
                d.with_timezone(&chrono::Local)
                    .format("%Y-%m-%d %H:%M")
                    .to_string()
            })
            .unwrap_or_default()
    }
}
fn one_line(value: &str) -> String {
    short(&value.split_whitespace().collect::<Vec<_>>().join(" "), 50)
}

#[cfg(test)]
mod prompt_tests {
    use super::*;
    use cplus_core::{AppInfo, Category, FileEntry, ScanResult};

    #[test]
    fn capacity_uses_only_pending_items_and_the_features_size_basis() {
        let mut pending = Pending {
            feature: Feature::Drive,
            action: Action::Delete,
            ids: vec!["chosen".into()],
        };
        let mut report = ScanResult {
            categories: vec![
                Category {
                    id: "chosen".into(),
                    bytes: 123,
                    ..Default::default()
                },
                Category {
                    id: "other".into(),
                    bytes: 999,
                    ..Default::default()
                },
            ],
            apps: vec![AppInfo {
                id: "chosen".into(),
                logical: 100,
                physical: 60,
                ..Default::default()
            }],
            ..Default::default()
        };
        assert_eq!(pending_capacity(&pending, &report), Some(123));
        pending.feature = Feature::Compression;
        assert_eq!(pending_capacity(&pending, &report), Some(60));
        pending.feature = Feature::AppMover;
        assert_eq!(pending_capacity(&pending, &report), Some(100));
        report.apps.clear();
        report.files.push(FileEntry {
            id: "file".into(),
            category: "chosen".into(),
            path: "example.zip".into(),
            size: 100,
            allocated: 80,
            identity: Default::default(),
            scope: String::new(),
            blocked: String::new(),
        });
        pending.ids.push("file".into());
        pending.feature = Feature::LargeFiles;
        assert_eq!(pending_capacity(&pending, &report), Some(100));
        pending.feature = Feature::Duplicates;
        assert_eq!(pending_capacity(&pending, &report), Some(80));
        pending.action = Action::Restore("history".into());
        assert_eq!(pending_capacity(&pending, &report), None);
        pending.action = Action::Delete;
        pending.ids.clear();
        assert_eq!(pending_capacity(&pending, &report), None);
    }

    #[test]
    fn prompt_size_keeps_short_messages_compact_and_gives_warnings_more_room() {
        let data = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!(
            "../../validation-output/prompt-size-test-{}",
            std::process::id()
        ));
        let (mut app, _) = App::new(crate::app::Config {
            preview: true,
            data: Some(data),
            ..Default::default()
        });
        app.info = "Short notice".into();
        app.issues.clear();
        assert_eq!(prompt_size(&app, false), iced::Size::new(360.0, 220.0));
        app.info = "Long notice ".repeat(20);
        assert_eq!(prompt_size(&app, false).height, 360.0);
        app.pending = Some(Pending {
            feature: Feature::Drive,
            action: Action::Delete,
            ids: vec!["cache".into()],
        });
        assert_eq!(prompt_size(&app, true), iced::Size::new(400.0, 280.0));
        app.pending.as_mut().unwrap().ids = vec!["drive:windows_old".into()];
        assert_eq!(prompt_size(&app, true).height, 360.0);
    }
}
