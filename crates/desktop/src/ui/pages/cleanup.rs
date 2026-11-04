use super::*;
use iced::widget::{Column, pick_list};
use iced::widget::{column, row};
use std::collections::BTreeMap;
pub fn view(app: &App) -> Element<'_, Message> {
    let mut output = column![hero(app)].spacing(14);
    if app.page == Feature::Drive {
        output = output.push(card(
            row![
                small(app.t("Scan range")),
                pick_list(app.ranges.clone(), Some(app.range.clone()), Message::Range)
                    .style(theme::select),
                toolbar(app),
                link(app.t("Reset defaults"), Message::Defaults)
            ]
            .spacing(14)
            .align_y(iced::Alignment::Center),
        ));
    } else {
        output = output.push(toolbar(app));
    }
    let Some(report) = app.report() else {
        return output.push(empty(app.t("Scan to see results"))).into();
    };
    let selected = app.choices(app.page);
    let mut groups = BTreeMap::new();
    for c in &report.categories {
        groups
            .entry(c.group.clone())
            .or_insert_with(Vec::new)
            .push(c);
    }
    let mut body = Column::new().spacing(13);
    let mut groups = groups.into_iter().collect::<Vec<_>>();
    groups.sort_by_key(|(group, categories)| {
        if app.page == Feature::Privacy {
            ["windows", "software", "browsers", "risk"]
                .iter()
                .position(|g| *g == group)
                .unwrap_or(99)
        } else if app.page == Feature::Chat {
            [
                "wechat", "qq", "whatsapp", "telegram", "discord", "slack", "teams", "signal",
            ]
            .iter()
            .position(|g| *g == categories[0].app)
            .unwrap_or(99)
        } else {
            [
                "缓存文件",
                "临时文件",
                "日志文件",
                "系统报告",
                "应用程序",
                "备份文件",
            ]
            .iter()
            .position(|g| *g == group)
            .unwrap_or(99)
        }
    });
    for (group, categories) in groups {
        let mut cards = vec![];
        let total = categories
            .iter()
            .map(|c| {
                if app.page == Feature::Privacy {
                    c.count
                } else {
                    c.bytes
                }
            })
            .sum::<u64>();
        for category in &categories {
            let id = category.id.clone();
            let status = match category.status.as_str() {
                "not_installed" => "Not installed",
                "data_not_found" => "Data not found",
                "unsupported_layout" => "Unsupported data layout",
                "system_cleanup_unavailable" => "Windows cleanup size is unavailable",
                _ => "",
            };
            let mut tile = row![
                check(
                    selected.contains(&id),
                    (!app.busy() && crate::app::category_selectable(report, category))
                        .then_some(Message::Toggle(id.clone()))
                ),
                category_icon(category),
                column![
                    label(app.t(&category.name)),
                    amount(
                        if category.status == "system_cleanup_unavailable" {
                            "—".into()
                        } else if app.page == Feature::Privacy {
                            format!("{} {}", category.count, app.t("entries"))
                        } else {
                            bytes(category.bytes)
                        },
                        18
                    )
                ]
                .spacing(6)
            ]
            .spacing(12)
            .align_y(iced::Alignment::Center);
            if category.view_only && category.id == "start_recent" {
                tile = tile.push(link(
                    app.t("View"),
                    Message::Open(
                        cplus_core::common::env_path(
                            "APPDATA",
                            cplus_core::common::home().join("AppData/Roaming"),
                        )
                        .join("Microsoft/Windows/Recent")
                        .display()
                        .to_string(),
                    ),
                ));
            }
            let mut tile = column![tile].spacing(5);
            if !status.is_empty() {
                tile = tile.push(small(app.t(status)));
            }
            cards.push(card(tile));
        }
        let heading = match group.as_str() {
            "windows" => app.t("Windows traces"),
            "software" => app.t("Application traces"),
            "browsers" => app.t("Browser history"),
            "risk" => app.t("Sensitive traces"),
            _ => app.t(&group),
        };
        let mut header = row![
            check(
                categories
                    .iter()
                    .filter(|c| crate::app::category_selectable(report, c))
                    .all(|c| selected.contains(&c.id))
                    && categories
                        .iter()
                        .any(|c| crate::app::category_selectable(report, c)),
                (!app.busy()).then_some(Message::CategoryGroup(group.clone()))
            ),
            bold(heading, 18),
            Space::new().width(Length::Fill),
            amount(
                if app.page == Feature::Privacy {
                    format!("{} {}", total, app.t("entries"))
                } else {
                    bytes(total)
                },
                16
            )
        ]
        .align_y(iced::Alignment::Center);
        if app.page == Feature::Chat {
            let id = categories[0].app.clone();
            header = header
                .push(link(
                    app.t("Choose folder"),
                    Message::FolderForChat(id.clone()),
                ))
                .push(link(app.t("Reset defaults"), Message::ResetChat(id)));
        }
        body = body.push(card(
            column![
                header,
                grid(cards, if app.page == Feature::Privacy { 3 } else { 2 })
            ]
            .spacing(14),
        ));
    }
    output
        .push(content_scroll(body))
        .height(Length::Fill)
        .into()
}
fn category_icon(category: &cplus_core::Category) -> Element<'static, Message> {
    if !category.icon.is_empty() {
        let path = category.icon.trim_start_matches("assets/");
        if cplus_core::resource(&format!("assets/{path}")).is_some() {
            return bitmap(path, 36);
        }
    }
    if category.id.starts_with("chat:wechat") {
        bitmap("icon-wechat.png", 36)
    } else if category.id.starts_with("chat:qq") {
        bitmap("icon-qq.png", 36)
    } else if cplus_core::resource(&format!("assets/privacy/{}.png", category.id)).is_some() {
        bitmap(&format!("privacy/{}.png", category.id), 36)
    } else {
        bitmap("icon-cache.png", 36)
    }
}
