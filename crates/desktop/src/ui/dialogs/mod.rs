use super::{pages, theme, widgets::*};
mod activity;
mod feedback;
mod settings;
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
        Dialog::Updates => updates(app),
        Dialog::History(f) => history(app, *f),
        Dialog::Residue => residue(app),
        Dialog::Confirm => confirm(app),
        Dialog::Information => information(app),
    };
    body = body.push(content);
    let mut footer = row![Space::new().width(Length::Fill)].spacing(12);
    match dialog {
        Dialog::Confirm => {
            footer = footer
                .push(btn(app.t("Back"), Some(Message::Dismiss(id)), false))
                .push(btn(
                    app.t("Confirm"),
                    (!app.busy()).then_some(Message::Confirm),
                    true,
                ))
        }
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
        _ => {
            footer = footer.push(btn(
                app.t(
                    if *dialog == Dialog::Updates || *dialog == Dialog::Information {
                        "OK"
                    } else {
                        "Close"
                    },
                ),
                Some(Message::Dismiss(id)),
                false,
            ))
        }
    }
    if matches!(dialog, Dialog::Residue) || *dialog == Dialog::Updates {
        let feature = match dialog {
            Dialog::Updates => Feature::Home,
            _ => app.progress.feature,
        };
        body = body.push(pages::progress_for(app, feature));
    }
    body = body.push(footer);
    container(body)
        .padding(if *dialog == Dialog::About { 20 } else { 26 })
        .width(Length::Fill)
        .height(Length::Fill)
        .style(theme::surface)
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
        Space::new().height(Length::Fill)
    ]
    .spacing(8)
    .align_x(iced::Alignment::Center)
    .into()
}
fn updates(app: &App) -> Element<'_, Message> {
    let mut content = column![small(format!(
        "{}  {}",
        app.t("Current version"),
        cplus_core::VERSION
    ))]
    .spacing(20);
    if let Some(release) = &app.release {
        content = content
            .push(bold(
                app.t(if release.newer {
                    "A newer version is available"
                } else {
                    "You are up to date"
                }),
                23,
            ))
            .push(label(format!(
                "{}  {}",
                app.t("Latest release"),
                release.tag
            )));
    } else if !app.update_error.is_empty() {
        content = content
            .push(bold(app.t("Unable to check for updates"), 22))
            .push(small(short(&app.t(&app.update_error), 250)));
    } else {
        content = content.push(bold(app.t("Checking the latest stable release…"), 21));
    }
    content.push(Space::new().height(Length::Fill)).into()
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
        return column![bold(app.t(title), 20), label(app.t("Windows may ask for administrator permission.")),
            small(app.t("Windows will perform this operation. Other selected cleanup items are not included."))
        ].spacing(14).into();
    }
    let text = match p.action {
        Action::Delete => "Files will be permanently deleted.",
        Action::System(_) => "Windows may ask for administrator permission.",
        _ => "Close the selected applications before continuing.",
    };
    let mut rows = column![
        bold(app.name(p.feature), 20),
        label(app.t(text)),
        small(format!("{} {}", app.t("Selected"), p.ids.len()))
    ]
    .spacing(14);
    if p.feature == Feature::Drive && p.ids.iter().any(|id| id == "drive:windows_old") {
        rows = rows.push(label(app.t("Deleting previous Windows installations prevents reverting to that Windows version.")).color(theme::CORAL));
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
            rows = rows.push(small(short(&name, 90)));
        }
    }
    if matches!(p.action, Action::Compress) {
        rows = rows.push(small(app.t(
            "Compression can slow application startup. Moving apps is preferred.",
        )));
    }
    scrollable(rows).height(Length::Fill).into()
}
fn information(app: &App) -> Element<'_, Message> {
    let mut rows = Column::new().spacing(12);
    if !app.info.is_empty() {
        rows = rows.push(label(app.t(&app.info)));
    }
    for issue in app.issues.iter().take(50) {
        rows = rows.push(
            column![
                small(short(&issue.path, 120)),
                label(short(&app.t(&issue.message), 350))
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
