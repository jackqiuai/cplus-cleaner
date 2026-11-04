use super::*;
use cplus_core::features::duplicates as engine;
use iced::widget::{button, column, pick_list, row, text_input};

pub fn view(app: &App) -> Element<'_, Message> {
    let report = app.report();
    let eligible = report
        .map(|r| engine::suggested_ids(r, None))
        .unwrap_or_default();
    let busy = app.busy();
    let minimums = app.duplicate_minimum_options();
    let drives: Vec<_> = app.disks.iter().map(|d| d.root.clone()).collect();
    let scope = column![
        row![
            pick_list(
                drives.clone(),
                Some(app.duplicate_drive.clone()),
                Message::DuplicateDrive
            )
            .placeholder(app.t("Scan range"))
            .text_size(13)
            .style(theme::settings_select)
            .menu_style(theme::settings_menu)
            .width(135),
            Space::new().width(Length::Fill),
            small(app.t("Minimum size")),
            pick_list(
                minimums.clone(),
                Some(minimums[app.duplicate_minimum].clone()),
                Message::DuplicateMinimum
            )
            .text_size(13)
            .style(theme::settings_select)
            .menu_style(theme::settings_menu)
            .width(130)
        ]
        .spacing(9)
        .align_y(iced::Alignment::Center)
    ]
    .spacing(8);
    let toolbar = row![
        button(label(app.t("Smart select")))
            .padding([7, 12])
            .style(theme::secondary)
            .on_press_maybe(
                (!busy && !eligible.is_empty()).then_some(Message::DuplicateSmartSelect)
            ),
        button(small(app.t("Select none")))
            .padding([7, 5])
            .style(theme::link)
            .on_press_maybe((!busy).then_some(Message::All(false))),
        text_input(&app.t("Search files"), &app.search)
            .on_input(Message::Search)
            .style(theme::search_input)
            .padding(8)
            .width(Length::Fill)
    ]
    .spacing(8)
    .align_y(iced::Alignment::Center);
    let mut output = column![hero(app), card(scope), card(toolbar)].spacing(10);
    output = output.push(content_scroll(results(app)));
    output
        .push(small(
            app.t("Keep at least one copy in every duplicate group."),
        ))
        .height(Length::Fill)
        .into()
}

use super::files::{category_name, file_icon, result_action, separator};
use cplus_core::{Category, FileEntry, ScanResult};
use std::collections::{BTreeMap, BTreeSet};
const SIZE_WIDTH: u32 = 86;
const ACTION_WIDTH: u32 = 108;

fn type_groups<'a>(
    report: &'a ScanResult,
    query: &str,
) -> BTreeMap<&'static str, Vec<&'a Category>> {
    let mut by_group: BTreeMap<&str, Vec<&FileEntry>> = BTreeMap::new();
    for file in &report.files {
        by_group.entry(&file.category).or_default().push(file);
    }
    let mut types: BTreeMap<&str, Vec<&Category>> = BTreeMap::new();
    for group in &report.categories {
        let Some(files) = by_group.get(group.id.as_str()) else {
            continue;
        };
        if !files.iter().any(|f| {
            query.is_empty()
                || cplus_core::common::display_path(&f.path)
                    .to_lowercase()
                    .contains(query)
        }) {
            continue;
        }
        let keeper = engine::keeper_id(report, group);
        let representative = files
            .iter()
            .find(|f| Some(f.id.as_str()) == keeper)
            .or_else(|| files.first());
        if let Some(file) = representative {
            let kind = cplus_core::features::large_files::category(&file.path).unwrap_or("other");
            types.entry(kind).or_default().push(group);
        }
    }
    types
}

fn reveal(file: &FileEntry) -> Element<'static, Message> {
    button(symbol("result-open-folder", 18))
        .padding(3)
        .style(theme::link)
        .on_press(Message::Reveal(file.path.clone()))
        .into()
}

pub fn expansion_ids(report: &ScanResult) -> BTreeSet<String> {
    type_groups(report, "")
        .into_iter()
        .flat_map(|(kind, groups)| {
            std::iter::once(format!("duplicate-type:{kind}"))
                .chain(groups.into_iter().map(|g| g.id.clone()))
        })
        .collect()
}

fn results(app: &App) -> Element<'static, Message> {
    let Some(report) = app.report() else {
        return empty(app.t("Scan to see results"));
    };
    let eligible = engine::suggested_ids(report, None);
    let selected = app.choices(Feature::Duplicates);
    let busy = app.busy();
    let all_checked = !eligible.is_empty() && eligible.iter().all(|id| selected.contains(id));
    let directories = expansion_ids(report);
    let all_expanded = !directories.is_empty() && directories.is_subset(&app.expanded);
    let arrow = if all_expanded {
        "<svg xmlns='http://www.w3.org/2000/svg' width='12' height='14' viewBox='0 0 12 14'><path d='m3 6 3-3 3 3M3 10l3-3 3 3' fill='none' stroke='#7a8fa3' stroke-linecap='round' stroke-linejoin='round'/></svg>"
    } else {
        "<svg xmlns='http://www.w3.org/2000/svg' width='12' height='14' viewBox='0 0 12 14'><path d='m3 4 3 3 3-3M3 8l3 3 3-3' fill='none' stroke='#7a8fa3' stroke-linecap='round' stroke-linejoin='round'/></svg>"
    };
    let mut rows = column![
        container(
            row![
                result_check(
                    all_checked,
                    !all_checked && eligible.iter().any(|id| selected.contains(id)),
                    (!busy && !eligible.is_empty())
                        .then(|| Message::DuplicateSelect(eligible.iter().cloned().collect()))
                ),
                button(
                    row![
                        small(app.t(if all_expanded {
                            "Collapse all"
                        } else {
                            "Expand all"
                        })),
                        iced::widget::svg(iced::widget::svg::Handle::from_memory(arrow.as_bytes()))
                            .width(12)
                            .height(14)
                    ]
                    .spacing(2)
                    .align_y(iced::Alignment::Center)
                )
                .padding([2, 0])
                .style(theme::link)
                .on_press_maybe(
                    (!busy && !directories.is_empty()).then_some(Message::DuplicateExpandAll)
                ),
                container(small(app.t("Name"))).width(Length::Fill),
                container(small(app.t("Size"))).width(SIZE_WIDTH),
                container(small(app.t("Actual usage"))).width(SIZE_WIDTH),
                container(small(app.t("Action"))).width(ACTION_WIDTH)
            ]
            .spacing(8)
            .align_y(iced::Alignment::Center)
        )
        .padding([8, 12])
        .style(theme::surface)
    ]
    .spacing(0);
    let query = app.search.to_lowercase();
    let mut types = type_groups(report, &query);
    let mut by_group: BTreeMap<&str, Vec<&FileEntry>> = BTreeMap::new();
    for file in &report.files {
        by_group.entry(&file.category).or_default().push(file);
    }
    if types.is_empty() {
        return empty(app.t(if report.cancelled {
            "Stopped"
        } else {
            "No matching items"
        }));
    }
    for kind in [
        "archives",
        "installers",
        "logs",
        "videos",
        "audio",
        "documents",
        "temporary",
        "images",
        "other",
    ] {
        let Some(groups) = types.remove(kind) else {
            continue;
        };
        let type_id = format!("duplicate-type:{kind}");
        let open = app.expanded.contains(&type_id);
        let group_ids: BTreeSet<_> = groups.iter().map(|g| g.id.as_str()).collect();
        let ids: Vec<_> = report
            .files
            .iter()
            .filter(|f| group_ids.contains(f.category.as_str()) && eligible.contains(&f.id))
            .map(|f| f.id.clone())
            .collect();
        let checked = !ids.is_empty() && ids.iter().all(|id| selected.contains(id));
        let has_selected = ids.iter().any(|id| selected.contains(id));
        let copies: u64 = groups.iter().map(|g| g.count).sum();
        let reclaim: u64 = groups.iter().map(|g| g.bytes).sum();
        rows = rows
            .push(
                container(
                    row![
                        result_check(
                            checked,
                            !checked && has_selected,
                            (!busy && !ids.is_empty())
                                .then(|| Message::DuplicateSelect(ids.clone()))
                        ),
                        button(label(if open { "⌄" } else { "›" }).size(19))
                            .padding(0)
                            .width(20)
                            .style(theme::link)
                            .on_press(Message::Expand(type_id.clone())),
                        button(symbol("result-folder", 28))
                            .padding(0)
                            .style(theme::link)
                            .on_press(Message::Expand(type_id.clone())),
                        button(
                            row![
                                bold(category_name(app, kind), 14),
                                small(format!(
                                    "({} {} · {} {})",
                                    groups.len(),
                                    app.t("Groups"),
                                    copies,
                                    app.t("Copies")
                                ))
                            ]
                            .spacing(6)
                            .align_y(iced::Alignment::Center)
                        )
                        .padding(0)
                        .style(theme::link)
                        .width(Length::Fill)
                        .on_press(Message::Expand(type_id.clone())),
                        container(small(app.t("Estimated reclaimable"))).width(SIZE_WIDTH),
                        container(amount(bytes(reclaim), 15)).width(SIZE_WIDTH),
                        container(
                            row![
                                result_action(
                                    app,
                                    "Delete",
                                    (!busy && has_selected)
                                        .then(|| Message::DuplicateDelete(ids.clone()))
                                ),
                                Space::new().width(24)
                            ]
                            .spacing(8)
                        )
                        .width(ACTION_WIDTH)
                    ]
                    .spacing(8)
                    .align_y(iced::Alignment::Center),
                )
                .padding([9, 12]),
            )
            .push(separator());
        if !open {
            continue;
        }
        let pages = groups.len().div_ceil(engine::PAGE_SIZE).max(1);
        let page = app
            .duplicate_entry_pages
            .get(&type_id)
            .copied()
            .unwrap_or(0)
            .min(pages - 1);
        for (index, group) in groups
            .iter()
            .enumerate()
            .skip(page * engine::PAGE_SIZE)
            .take(engine::PAGE_SIZE)
        {
            let group_open = app.expanded.contains(&group.id);
            let files = &by_group[group.id.as_str()];
            let ids: Vec<_> = files
                .iter()
                .filter(|f| eligible.contains(&f.id))
                .map(|f| f.id.clone())
                .collect();
            let checked = !ids.is_empty() && ids.iter().all(|id| selected.contains(id));
            let has_selected = ids.iter().any(|id| selected.contains(id));
            rows = rows
                .push(
                    container(
                        row![
                            Space::new().width(18),
                            result_check(
                                checked,
                                !checked && has_selected,
                                (!busy && !ids.is_empty())
                                    .then_some(Message::Group(group.id.clone()))
                            ),
                            button(label(if group_open { "⌄" } else { "›" }).size(18))
                                .padding(0)
                                .width(20)
                                .style(theme::link)
                                .on_press(Message::Expand(group.id.clone())),
                            button(symbol("result-copy", 22))
                                .padding(0)
                                .style(theme::link)
                                .on_press(Message::Expand(group.id.clone())),
                            button(
                                row![
                                    small(format!(
                                        "{} {} · {}",
                                        app.t("Duplicate group"),
                                        index + 1,
                                        short(&group.name, 35)
                                    )),
                                    small(format!("({} {})", group.count, app.t("Copies")))
                                ]
                                .spacing(6)
                            )
                            .padding(0)
                            .width(Length::Fill)
                            .style(theme::link)
                            .on_press(Message::Expand(group.id.clone())),
                            container(small(app.t("Estimated reclaimable"))).width(SIZE_WIDTH),
                            container(amount(bytes(group.bytes), 14)).width(SIZE_WIDTH),
                            container(
                                row![
                                    result_action(
                                        app,
                                        "Delete",
                                        (!busy && has_selected).then_some(Message::Request(
                                            Action::Delete,
                                            Some(group.id.clone())
                                        ))
                                    ),
                                    Space::new().width(24)
                                ]
                                .spacing(8)
                            )
                            .width(ACTION_WIDTH)
                        ]
                        .spacing(8)
                        .align_y(iced::Alignment::Center),
                    )
                    .padding([8, 12])
                    .style(theme::surface),
                )
                .push(separator());
            if !group_open {
                continue;
            }
            let entries: Vec<_> = files
                .iter()
                .copied()
                .filter(|f| {
                    query.is_empty()
                        || cplus_core::common::display_path(&f.path)
                            .to_lowercase()
                            .contains(&query)
                })
                .collect();
            let entry_pages = entries.len().div_ceil(engine::ENTRY_PAGE_SIZE).max(1);
            let entry_page = app
                .duplicate_entry_pages
                .get(&group.id)
                .copied()
                .unwrap_or(0)
                .min(entry_pages - 1);
            for file in entries
                .iter()
                .skip(entry_page * engine::ENTRY_PAGE_SIZE)
                .take(engine::ENTRY_PAGE_SIZE)
            {
                let retained = engine::is_retained(report, &file.id);
                let enabled = !busy && eligible.contains(&file.id);
                let path = cplus_core::common::display_path(&file.path);
                let mut details = column![
                    label(short(
                        &file.path.file_name().unwrap_or_default().to_string_lossy(),
                        48
                    ))
                    .size(13),
                    iced::widget::tooltip(
                        small(short(&path, 65)),
                        container(small(path)).padding(8).style(theme::surface),
                        iced::widget::tooltip::Position::Top
                    )
                ]
                .spacing(3)
                .width(Length::Fill);
                if !file.blocked.is_empty() {
                    details = details.push(small(app.t(&file.blocked)).color(theme::CORAL));
                } else if retained {
                    details = details.push(small(app.t("Keep this copy")).color(theme::MINT));
                }
                let estimate = report
                    .duplicate_scan
                    .as_ref()
                    .is_some_and(|s| s.estimated_files.contains(&file.id));
                rows = rows
                    .push(
                        container(
                            row![
                                Space::new().width(54),
                                result_check(
                                    selected.contains(&file.id),
                                    false,
                                    enabled.then_some(Message::Toggle(file.id.clone()))
                                ),
                                file_icon(&file.path),
                                details,
                                container(small(bytes(file.size))).width(SIZE_WIDTH),
                                container(small(format!(
                                    "{}{}",
                                    if estimate { "≈ " } else { "" },
                                    bytes(file.allocated)
                                )))
                                .width(SIZE_WIDTH),
                                container(
                                    row![
                                        result_action(
                                            app,
                                            "Delete",
                                            enabled.then_some(Message::Request(
                                                Action::Delete,
                                                Some(file.id.clone())
                                            ))
                                        ),
                                        reveal(file)
                                    ]
                                    .spacing(8)
                                )
                                .width(ACTION_WIDTH)
                            ]
                            .spacing(8)
                            .align_y(iced::Alignment::Center),
                        )
                        .padding([8, 12]),
                    )
                    .push(separator());
            }
            if entry_pages > 1 {
                rows = rows.push(pager(app, entry_page, entry_pages, |p| {
                    Message::DuplicateEntryPage(group.id.clone(), p)
                }));
            }
        }
        if pages > 1 {
            rows = rows.push(pager(app, page, pages, |p| {
                Message::DuplicateEntryPage(type_id.clone(), p)
            }));
        }
    }
    container(rows)
        .width(Length::Fill)
        .style(theme::card)
        .into()
}

pub(super) fn statistics(app: &App) -> String {
    let Some(report) = app.reports.get(&Feature::Duplicates) else {
        return String::new();
    };
    let Some(s) = &report.duplicate_scan else {
        return String::new();
    };
    let estimates = if s.estimated_files.is_empty() {
        String::new()
    } else {
        format!(
            " · {} {}",
            s.estimated_files.len(),
            app.t("Usage estimates")
        )
    };
    format!(
        "{} {} · {} {} · {} {} · {} {} · {} {} · {} {} · {} {}{}",
        app.t("Checked"),
        s.checked,
        app.t("Skipped"),
        s.skipped,
        app.t("Unreadable"),
        s.unreadable,
        app.t("Changed files"),
        s.changed,
        app.t("Hard links"),
        s.hard_links,
        app.t("Disk usage"),
        bytes(report.allocated_bytes()),
        app.t("Estimated reclaimable"),
        bytes(report.bytes()),
        estimates
    )
}
fn pager(
    app: &App,
    page: usize,
    pages: usize,
    message: impl Fn(usize) -> Message,
) -> Element<'static, Message> {
    row![
        Space::new().width(Length::Fill),
        button(small(app.t("Previous")))
            .padding([4, 8])
            .style(theme::secondary)
            .on_press_maybe((page > 0).then(|| message(page - 1))),
        small(format!("{} / {}", page + 1, pages)),
        button(small(app.t("Next")))
            .padding([4, 8])
            .style(theme::secondary)
            .on_press_maybe((page + 1 < pages).then(|| message(page + 1)))
    ]
    .spacing(10)
    .align_y(iced::Alignment::Center)
    .into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn types_preserve_duplicate_groups_and_follow_the_retained_copy() {
        let mut report = ScanResult::default();
        for (id, name, category) in [
            ("keeper", "original.zip", "archive-group"),
            ("copy", "backup.dat", "archive-group"),
            ("setup", "setup.exe", "installer-group"),
            ("setup-copy", "setup-copy.exe", "installer-group"),
        ] {
            report.files.push(FileEntry {
                id: id.into(),
                path: std::path::PathBuf::from(r"C:\Downloads").join(name),
                category: category.into(),
                size: 10,
                allocated: 10,
                identity: Default::default(),
                scope: "scope".into(),
                blocked: String::new(),
            });
        }
        report.categories = vec![
            Category {
                id: "archive-group".into(),
                file_ids: vec!["keeper".into(), "copy".into()],
                ..Default::default()
            },
            Category {
                id: "installer-group".into(),
                file_ids: vec!["setup".into(), "setup-copy".into()],
                ..Default::default()
            },
        ];
        report.duplicate_scan = Some(engine::Summary {
            keepers: [
                ("archive-group".into(), "keeper".into()),
                ("installer-group".into(), "setup".into()),
            ]
            .into_iter()
            .collect(),
            ..Default::default()
        });
        let types = type_groups(&report, "");
        assert_eq!(types.len(), 2);
        assert_eq!(types["archives"][0].id, "archive-group");
        assert_eq!(types["installers"][0].id, "installer-group");
        assert_eq!(types.values().map(Vec::len).sum::<usize>(), 2);
        let filtered = type_groups(&report, "backup.dat");
        assert_eq!(filtered.len(), 1);
        assert_eq!(filtered["archives"][0].file_ids.len(), 2);
        assert!(type_groups(&report, "not-present").is_empty());
    }
}
