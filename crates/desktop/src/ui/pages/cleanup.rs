use super::*;
use iced::widget::{Column, pick_list, responsive, tooltip};
use iced::widget::{column, row};
use std::{cell::RefCell, collections::BTreeMap};

fn name_width(value: &str) -> f32 {
    use iced::advanced::text::{Paragraph, Renderer};
    <iced::Renderer as Renderer>::Paragraph::with_text(iced::advanced::Text {
        content: value,
        bounds: iced::Size::INFINITE,
        size: 13.0.into(),
        line_height: Default::default(),
        font: crate::ui::fonts::regular(),
        align_x: Default::default(),
        align_y: iced::alignment::Vertical::Top,
        shaping: iced::widget::text::Shaping::Advanced,
        wrapping: iced::widget::text::Wrapping::None,
    })
    .min_width()
}
fn elided_name(value: &str, width: f32) -> String {
    // Avoid reshaping unchanged names on every 120ms UI refresh.
    thread_local! { static CACHE: RefCell<BTreeMap<(String, u32), String>> = const { RefCell::new(BTreeMap::new()) }; }
    let width = width.max(0.0).floor() as u32;
    CACHE.with_borrow_mut(|cache| {
        let key = (value.to_owned(), width);
        if let Some(cached) = cache.get(&key) {
            return cached.clone();
        }
        let result = if name_width(value) <= width as f32 {
            value.into()
        } else if name_width("…") > width as f32 {
            String::new()
        } else {
            let boundaries: Vec<_> = value
                .char_indices()
                .map(|(i, _)| i)
                .chain(std::iter::once(value.len()))
                .collect();
            let (mut low, mut high) = (0, boundaries.len() - 1);
            while low < high {
                let mid = (low + high).div_ceil(2);
                if name_width(&format!("{}…", &value[..boundaries[mid]])) <= width as f32 {
                    low = mid;
                } else {
                    high = mid - 1;
                }
            }
            format!("{}…", &value[..boundaries[low]])
        };
        if cache.len() >= 512 {
            cache.clear();
        }
        cache.insert(key, result.clone());
        result
    })
}
pub(super) fn compact(feature: Feature) -> bool {
    matches!(feature, Feature::Drive | Feature::Chat)
}
pub fn view(app: &App) -> Element<'_, Message> {
    if compact(app.page) {
        responsive(move |size| content(app, compact_columns(size.width))).into()
    } else {
        content(app, if app.page == Feature::Privacy { 3 } else { 2 })
    }
}
fn compact_columns(width: f32) -> usize {
    if width >= 840.0 {
        4
    } else if width >= 620.0 {
        3
    } else {
        2
    }
}
fn content(app: &App, columns: usize) -> Element<'_, Message> {
    let compact = compact(app.page);
    let mut output = column![hero(app)].spacing(if compact { 10 } else { 14 });
    if app.page == Feature::Drive {
        output = output.push(
            container(
                row![
                    small(app.t("Scan range")),
                    pick_list(app.ranges.clone(), Some(app.range.clone()), Message::Range)
                        .style(theme::select),
                    toolbar(app),
                    link(app.t("Reset defaults"), Message::Defaults)
                ]
                .spacing(12)
                .align_y(iced::Alignment::Center),
            )
            .padding([8, 12])
            .width(Length::Fill)
            .style(theme::card),
        );
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
    let mut body = Column::new().spacing(if compact { 10 } else { 13 });
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
            let full_name = app.t(&category.name);
            let name: Element<'_, Message> = if compact {
                responsive(move |size| {
                    let shown = elided_name(&full_name, size.width);
                    let truncated = shown != full_name;
                    let text = label(shown)
                        .size(13)
                        .wrapping(iced::widget::text::Wrapping::None);
                    if truncated {
                        tooltip(
                            text,
                            container(label(full_name.clone()).size(13))
                                .padding(8)
                                .max_width(360)
                                .style(theme::card),
                            tooltip::Position::Top,
                        )
                        .delay(std::time::Duration::from_millis(300))
                        .into()
                    } else {
                        text.into()
                    }
                })
                .height(16)
                .into()
            } else {
                label(full_name).into()
            };
            let value = amount(
                if compact && category.view_only && !status.is_empty() {
                    app.t(if category.status == "unsupported_layout" {
                        "Unavailable"
                    } else {
                        status
                    })
                } else if category.status == "system_cleanup_unavailable" {
                    if compact {
                        format!("— · {}", app.t("Unavailable"))
                    } else {
                        "—".into()
                    }
                } else if app.page == Feature::Privacy {
                    format!("{} {}", category.count, app.t("entries"))
                } else {
                    bytes(category.bytes)
                },
                if compact { 15 } else { 18 },
            );
            let value: Element<'_, Message> = if compact && !status.is_empty() {
                tooltip(
                    value,
                    container(small(app.t(status)))
                        .padding(8)
                        .max_width(360)
                        .style(theme::card),
                    tooltip::Position::Top,
                )
                .into()
            } else {
                value.into()
            };
            let mut tile = row![
                check(
                    selected.contains(&id),
                    (!app.busy() && crate::app::category_selectable(report, category))
                        .then_some(Message::Toggle(id.clone()))
                ),
                category_icon(category, if compact { 24 } else { 36 }),
                column![name, value]
                    .spacing(if compact { 2 } else { 6 })
                    .width(if compact {
                        Length::Fill
                    } else {
                        Length::Shrink
                    })
            ]
            .spacing(if compact { 7 } else { 12 })
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
            if !compact && !status.is_empty() {
                tile = tile.push(small(app.t(status)));
            }
            cards.push(if compact {
                container(tile)
                    .padding(8)
                    .center_y(56)
                    .width(Length::Fill)
                    .style(theme::card)
                    .into()
            } else {
                card(tile)
            });
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
            bold(heading, if compact { 15 } else { 18 }),
            Space::new().width(Length::Fill),
            amount(
                if app.page == Feature::Privacy {
                    format!("{} {}", total, app.t("entries"))
                } else {
                    bytes(total)
                },
                if compact { 15 } else { 16 }
            )
        ]
        .spacing(if compact { 8 } else { 0 })
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
        let group_body = column![
            header,
            grid_spaced(cards, columns, if compact { 8 } else { 12 })
        ]
        .spacing(if compact { 8 } else { 14 });
        body = body.push(if compact {
            container(group_body)
                .padding(10)
                .width(Length::Fill)
                .style(theme::card)
                .into()
        } else {
            card(group_body)
        });
    }
    output
        .push(content_scroll(body))
        .height(Length::Fill)
        .into()
}
fn category_icon(category: &cplus_core::Category, size: u32) -> Element<'static, Message> {
    if !category.icon.is_empty() {
        let path = category.icon.trim_start_matches("assets/");
        if cplus_core::resource(&format!("assets/{path}")).is_some() {
            return bitmap(path, size);
        }
    }
    if category.id.starts_with("chat:wechat") {
        bitmap("icon-wechat.png", size)
    } else if category.id.starts_with("chat:qq") {
        bitmap("icon-qq.png", size)
    } else if cplus_core::resource(&format!("assets/privacy/{}.png", category.id)).is_some() {
        bitmap(&format!("privacy/{}.png", category.id), size)
    } else {
        bitmap("icon-cache.png", size)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn long_names_are_measured_and_elided_without_splitting_utf8() {
        assert_eq!(elided_name("缩略图缓存", 150.0), "缩略图缓存");
        assert_ne!(
            elided_name("Terminal Server Client缓存", 100.0),
            "Terminal Server Client缓存"
        );
        for name in [
            "Terminal Server Client缓存",
            "系统以及程序崩溃dmp文件",
            "Windows Defender扫描历史",
        ] {
            assert_eq!(elided_name(name, name_width(name).ceil() + 1.0), name);
            for width in [70.0, 100.0, 140.0] {
                let shown = elided_name(name, width);
                assert!(name_width(&shown) <= width + 0.1);
                if shown != name {
                    assert!(shown.ends_with('…'));
                    assert!(name.starts_with(shown.trim_end_matches('…')));
                }
                assert_eq!(elided_name(name, width), shown);
            }
        }
        assert_eq!(elided_name("短名称", 0.0), "");
    }
    #[test]
    fn drive_and_chat_share_compact_layout_without_changing_privacy() {
        assert!(compact(Feature::Drive));
        assert!(compact(Feature::Chat));
        assert!(!compact(Feature::Privacy));
    }
    #[test]
    fn compact_grid_uses_four_columns_without_squeezing_narrow_windows() {
        assert_eq!(compact_columns(952.0), 4);
        assert_eq!(compact_columns(840.0), 4);
        assert_eq!(compact_columns(839.0), 3);
        assert_eq!(compact_columns(620.0), 3);
        assert_eq!(compact_columns(619.0), 2);
        assert_eq!(compact_columns(320.0), 2);
    }
}
