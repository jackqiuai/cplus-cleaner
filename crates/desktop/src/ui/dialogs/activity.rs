use super::*;
use cplus_core::Activity;
use iced::{
    Border, Color,
    widget::{button, column, pick_list, tooltip},
};
use std::collections::BTreeSet;

#[derive(Clone, PartialEq, Eq)]
struct Choice {
    key: String,
    label: String,
}
impl std::fmt::Display for Choice {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.label)
    }
}

fn action_key(action: &str) -> &'static str {
    match action {
        "software_move" => "App Mover",
        "software_compress" => "App Compression",
        "software_restore" => "Restore",
        "software_uninstall" => "App Uninstaller",
        "large_file_move" | "large_file_delete" | "large_files_delete" => "Large File Mover",
        "duplicate_cleanup" | "duplicates_delete" => "Duplicate Cleanup",
        "chat_cleanup" | "chat_delete" => "Chat Cleanup",
        "privacy_cleanup" | "privacy_delete" => "Privacy Cleanup",
        "system_action" | "system_delete" => "System Slimming",
        "drive_delete" | "home_delete" => "C drive cleanup",
        _ => "Other operations",
    }
}

fn matches(activity: &Activity, action: &str, result: Option<bool>) -> bool {
    (action.is_empty() || action_key(&activity.action) == action)
        && result.is_none_or(|ok| activity.ok == ok)
}

pub(super) fn view(app: &App) -> Element<'_, Message> {
    let choice = |key: &str| Choice {
        key: key.into(),
        label: app.t(key),
    };
    let mut actions = vec![choice("All operations")];
    actions.extend(
        app.activities
            .iter()
            .map(|a| action_key(&a.action))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .map(choice),
    );
    let selected = choice(if app.activity_action.is_empty() {
        "All operations"
    } else {
        &app.activity_action
    });
    let results = vec![
        choice("All results"),
        choice("Success"),
        choice("Partial completion"),
    ];
    let selected_result = choice(match app.activity_result {
        None => "All results",
        Some(true) => "Success",
        Some(false) => "Partial completion",
    });
    let toolbar = row![
        pick_list(actions, Some(selected), |c| Message::ActivityAction(
            if c.key == "All operations" {
                String::new()
            } else {
                c.key
            }
        ))
        .width(155)
        .text_size(14)
        .padding(12)
        .style(theme::select),
        pick_list(results, Some(selected_result), |c| Message::ActivityResult(
            match c.key.as_str() {
                "Success" => Some(true),
                "Partial completion" => Some(false),
                _ => None,
            }
        ))
        .width(145)
        .text_size(14)
        .padding(12)
        .style(theme::select),
        Space::new().width(Length::Fill),
        record_button(
            app.t("Delete records"),
            (!app.activity_selected.is_empty()).then_some(Message::DeleteActivity),
            false
        ),
        record_button(
            app.t("Clear all"),
            (!app.activities.is_empty()).then_some(Message::ClearActivity),
            true
        )
    ]
    .spacing(12)
    .align_y(iced::Alignment::Center);
    let visible = app
        .activities
        .iter()
        .rev()
        .filter(|a| matches(a, &app.activity_action, app.activity_result))
        .collect::<Vec<_>>();
    let ids = visible.iter().map(|a| a.id.clone()).collect::<Vec<_>>();
    let selected_count = ids
        .iter()
        .filter(|id| app.activity_selected.contains(*id))
        .count();
    let header = row![
        result_check(
            !ids.is_empty() && selected_count == ids.len(),
            selected_count > 0 && selected_count < ids.len(),
            (!ids.is_empty()).then_some(Message::SelectActivities(ids))
        ),
        bold(app.t("Record time"), 14)
            .color(theme::MUTED)
            .width(125),
        bold(app.t("Operation"), 14).color(theme::MUTED).width(155),
        bold(app.t("Result"), 14).color(theme::MUTED).width(112),
        bold(app.t("Summary"), 14)
            .color(theme::MUTED)
            .width(Length::Fill)
    ]
    .spacing(14)
    .align_y(iced::Alignment::Center);
    let mut rows = Column::new();
    if visible.is_empty() {
        rows = rows.push(empty(app.t(if app.activities.is_empty() {
            "No records"
        } else {
            "No matching items"
        })));
    }
    for activity in &visible {
        let selected = app.activity_selected.contains(&activity.id);
        let has_name = matches!(
            activity.action.as_str(),
            "software_move" | "software_compress" | "software_restore"
        ) && !activity.summary.trim().is_empty()
            && activity.summary != activity.action;
        let mut action = app.t(action_key(&activity.action));
        if has_name {
            action.push_str(&format!(" · {}", activity.summary));
        }
        let time = chrono::NaiveDateTime::parse_from_str(&activity.time, "%Y-%m-%d %H:%M:%S")
            .map(|t| t.format("%m-%d %H:%M").to_string())
            .unwrap_or_else(|_| activity.time.clone());
        let badge_color = if activity.ok {
            theme::MINT
        } else {
            Color::from_rgb8(0xce, 0x8b, 0x14)
        };
        let badge_bg = if activity.ok {
            Color::from_rgb8(0xd8, 0xfa, 0xed)
        } else {
            Color::from_rgb8(0xff, 0xf0, 0xcb)
        };
        let badge = container(
            bold(
                app.t(if activity.ok {
                    "Success"
                } else {
                    "Partial completion"
                }),
                13,
            )
            .color(badge_color),
        )
        .padding([6, 10])
        .style(move |_| container::Style {
            background: Some(badge_bg.into()),
            border: Border {
                radius: 6.0.into(),
                ..Default::default()
            },
            ..Default::default()
        });
        let content = row![
            result_check(
                selected,
                false,
                Some(Message::SelectActivity(activity.id.clone()))
            ),
            tooltip(
                small(time).size(14).width(125),
                label(activity.time.clone()),
                tooltip::Position::Top
            ),
            tooltip(
                label(short(&action, 10)).width(155),
                label(action),
                tooltip::Position::Top
            ),
            container(badge).width(112),
            summary(app, activity).width(Length::Fill)
        ]
        .spacing(14)
        .align_y(iced::Alignment::Center);
        rows = rows
            .push(
                container(content)
                    .padding([12, 12])
                    .width(Length::Fill)
                    .style(move |_| container::Style {
                        background: Some(if selected { theme::PALE } else { Color::WHITE }.into()),
                        ..Default::default()
                    }),
            )
            .push(
                container(Space::new().height(1))
                    .width(Length::Fill)
                    .style(|_| container::Style {
                        background: Some(theme::LINE.into()),
                        ..Default::default()
                    }),
            );
    }
    let table = container(column![
        container(header)
            .padding([12, 12])
            .width(Length::Fill)
            .style(|_| container::Style {
                background: Some(theme::BG.into()),
                ..Default::default()
            }),
        scrollable(rows).height(Length::Fill)
    ])
    .height(Length::Fill)
    .style(|_| container::Style {
        background: Some(Color::WHITE.into()),
        border: Border {
            color: theme::LINE,
            width: 1.0,
            radius: 9.0.into(),
        },
        ..Default::default()
    });
    let count = if app.activity_action.is_empty() && app.activity_result.is_none() {
        format!(
            "{} {} {}",
            app.t("Record count"),
            app.activities.len(),
            app.t("records")
        )
    } else {
        format!(
            "{} {} / {} {}",
            app.t("Showing"),
            visible.len(),
            app.activities.len(),
            app.t("records")
        )
    };
    container(column![toolbar, table, small(count).size(14)].spacing(16))
        .padding(20)
        .height(Length::Fill)
        .width(Length::Fill)
        .style(|_| container::Style {
            background: Some(Color::WHITE.into()),
            ..Default::default()
        })
        .into()
}

fn record_button(text: String, action: Option<Message>, danger: bool) -> Element<'static, Message> {
    let color = if action.is_none() {
        theme::MUTED
    } else if danger {
        theme::CORAL
    } else {
        theme::MINT
    };
    button(
        label(text)
            .color(color)
            .align_x(iced::Alignment::Center)
            .width(Length::Fill),
    )
    .width(112)
    .padding([12, 10])
    .on_press_maybe(action)
    .style(move |t, status| {
        let mut style = theme::secondary(t, status);
        let color = if matches!(status, iced::widget::button::Status::Disabled) {
            theme::MUTED
        } else if danger {
            theme::CORAL
        } else {
            theme::MINT
        };
        style.text_color = color;
        if !matches!(status, iced::widget::button::Status::Disabled) {
            style.border.color = color;
        }
        if matches!(status, iced::widget::button::Status::Active) {
            style.background = Some(Color::WHITE.into());
        }
        style
    })
    .into()
}

fn summary(app: &App, activity: &Activity) -> iced::widget::Row<'static, Message> {
    let mut content = row![].spacing(5).align_y(iced::Alignment::Center);
    if activity.bytes > 0 {
        content = content
            .push(label(app.t(if activity.action == "software_compress" {
                "Space saved"
            } else if activity.action == "large_file_move" {
                "Migrated"
            } else if activity.action.contains("move") {
                "Moved"
            } else {
                "Cleaned"
            })))
            .push(bold(bytes(activity.bytes), 14).color(theme::CORAL));
    } else if activity.action == "system_action" {
        let key = match activity.summary.as_str() {
            "EmptyRecycleBin" => "Empty recycle bin",
            "HibernateOff" => "Disable hibernation",
            "HibernateOn" => "Enable hibernation",
            "PagefileSettings" => "Virtual memory settings",
            "AnalyzeComponents" => "Analyze components",
            "CleanupComponents" => "Clean components",
            "ClearDeliveryOptimization" => "Windows Delivery Optimization files",
            "ClearWindowsUpdate" => "Windows Update cleanup",
            "ClearPreviousWindows" => "Previous Windows installations",
            _ => &activity.summary,
        };
        content = content.push(label(app.t(key)));
    } else if !activity.summary.is_empty() && activity.summary != activity.action {
        content = content.push(tooltip(
            label(short(&app.t(&activity.summary), 26)),
            label(app.t(&activity.summary)),
            tooltip::Position::Top,
        ));
    } else {
        content = content
            .push(label(app.t(if activity.action.starts_with("privacy") {
                "Cleaned"
            } else {
                "Completed"
            })))
            .push(bold(format!("{} {}", activity.count, app.t("items")), 14).color(theme::CORAL));
    }
    content
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn filters_combine_operation_and_result_without_guessing_unknown_actions() {
        let moved = Activity {
            action: "software_move".into(),
            ok: true,
            ..Default::default()
        };
        let partial = Activity {
            action: "chat_cleanup".into(),
            ok: false,
            ..Default::default()
        };
        assert!(matches(&moved, "", None));
        assert!(matches(&moved, "App Mover", Some(true)));
        assert!(!matches(&moved, "App Mover", Some(false)));
        assert!(!matches(&moved, "Chat Cleanup", None));
        assert!(matches(&partial, "Chat Cleanup", Some(false)));
        assert_eq!(action_key("drive_delete"), "C drive cleanup");
        assert_eq!(action_key("duplicate_cleanup"), "Duplicate Cleanup");
        assert_eq!(action_key("unrecognized"), "Other operations");
    }
}
