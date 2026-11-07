use super::{theme, widgets::*};
use crate::{
    app::{App, Message},
    monitor::Tab,
};
use cplus_platform_windows::monitor::taskbar::{percentage, speed};
use iced::{
    Element, Length, Point, Rectangle, Renderer, Theme, mouse,
    widget::{
        self, Column, Row, button, canvas, column, container, image, progress_bar, row, rule,
        scrollable,
    },
};

fn pair<'a>(name: String, value: String) -> Element<'a, Message> {
    row![small(name).width(Length::Fill), label(value).size(13)]
        .spacing(8)
        .into()
}
fn card<'a>(content: impl Into<Element<'a, Message>>) -> Element<'a, Message> {
    container(content)
        .padding(14)
        .width(Length::Fill)
        .style(theme::card)
        .into()
}
fn temperature<'a>(app: &App, value: Option<f64>) -> Element<'a, Message> {
    let text = small(
        value
            .map(|v| format!("{v:.0}°C"))
            .unwrap_or_else(|| "--".into()),
    )
    .color(theme::INK);
    if value.is_some() {
        return text.into();
    }
    widget::tooltip(
        text,
        container(small(app.t(
            "Temperature sensor unavailable for the current hardware or permissions",
        )))
        .padding(8)
        .style(theme::card),
        widget::tooltip::Position::Top,
    )
    .into()
}
fn cpu_temperature<'a>(app: &App) -> Element<'a, Message> {
    if app.monitor.sample.temperatures.cpu.is_none()
        && cplus_platform_windows::monitor::cpu_sensor::supported()
    {
        button(small(app.t("Enable CPU temperature")).color(theme::MINT))
            .padding(0)
            .style(theme::menu_item)
            .on_press(Message::EnableCpuTemperature)
            .into()
    } else {
        temperature(app, app.monitor.sample.temperatures.cpu)
    }
}
fn meter<'a>(
    app: &App,
    title: &str,
    value: Option<f64>,
    caption: impl Into<Element<'a, Message>>,
) -> Element<'a, Message> {
    card(
        column![
            row![
                bold(app.t(title), 15).width(Length::Fill),
                bold(percentage(value), 26)
            ],
            progress_bar(0.0..=100.0, value.unwrap_or(0.0) as f32)
                .girth(6)
                .style(|_| widget::progress_bar::Style {
                    background: theme::PALE.into(),
                    bar: theme::MINT.into(),
                    border: iced::Border {
                        radius: 3.0.into(),
                        ..Default::default()
                    }
                }),
            caption.into()
        ]
        .spacing(10),
    )
}
struct Chart {
    x: Vec<f32>,
    lines: Vec<(Vec<Option<f64>>, iced::Color)>,
    percent: bool,
}
impl canvas::Program<Message> for Chart {
    type State = ();
    fn draw(
        &self,
        _: &(),
        renderer: &Renderer,
        _: &Theme,
        bounds: Rectangle,
        _: mouse::Cursor,
    ) -> Vec<canvas::Geometry> {
        let mut frame = canvas::Frame::new(renderer, bounds.size());
        let max = if self.percent {
            100.0
        } else {
            self.lines
                .iter()
                .flat_map(|(v, _)| v.iter().flatten())
                .copied()
                .fold(1.0, f64::max)
        };
        let baseline = canvas::Path::line(
            Point::new(0.0, bounds.height - 1.0),
            Point::new(bounds.width, bounds.height - 1.0),
        );
        frame.stroke(&baseline, canvas::Stroke::default().with_color(theme::LINE));
        for (values, color) in &self.lines {
            let path = canvas::Path::new(|p| {
                let mut active = false;
                for (i, value) in values.iter().enumerate() {
                    if let Some(value) = value {
                        let point = Point::new(
                            bounds.width * self.x[i],
                            (bounds.height - 4.0) * (1.0 - (value / max).clamp(0.0, 1.0) as f32)
                                + 2.0,
                        );
                        if active {
                            p.line_to(point);
                        } else {
                            p.move_to(point);
                            active = true;
                        }
                    } else {
                        active = false;
                    }
                }
            });
            frame.stroke(
                &path,
                canvas::Stroke::default().with_width(1.5).with_color(*color),
            );
        }
        vec![frame.into_geometry()]
    }
}
fn chart(app: &App, tab: Tab, height: f32) -> Element<'static, Message> {
    let points = &app.monitor.points;
    let lines = match tab {
        Tab::Cpu => vec![(points.iter().map(|p| p.cpu).collect(), theme::MINT)],
        Tab::Disk => vec![
            (points.iter().map(|p| p.read).collect(), theme::MINT),
            (points.iter().map(|p| p.write).collect(), theme::CORAL),
        ],
        _ => vec![
            (points.iter().map(|p| p.up).collect(), theme::MINT),
            (
                points.iter().map(|p| p.down).collect(),
                iced::Color::from_rgb8(70, 145, 215),
            ),
        ],
    };
    canvas(Chart {
        x: points
            .iter()
            .map(|p| (1.0 - p.at.elapsed().as_secs_f32() / 60.0).clamp(0.0, 1.0))
            .collect(),
        lines,
        percent: tab == Tab::Cpu,
    })
    .width(Length::Fill)
    .height(height)
    .into()
}
fn disk(app: &App, overview: bool) -> Element<'_, Message> {
    let sample = &app.monitor.sample;
    let caption = sample
        .disk
        .as_ref()
        .map(|d| {
            format!(
                "{} {} / {}",
                app.t("Available"),
                bytes(d.free),
                bytes(d.total)
            )
        })
        .unwrap_or_else(|| app.t("Unavailable"));
    let caption: Element<'_, Message> = if overview {
        row![
            small(caption).width(Length::Fill),
            row![
                small(app.t("Temperature")),
                temperature(app, sample.temperatures.disk)
            ]
            .spacing(4)
        ]
        .spacing(6)
        .into()
    } else {
        small(caption).into()
    };
    let heading = meter(app, "System drive space", sample.disk_percent(), caption);
    let heading: Element<'_, Message> = if overview {
        button(heading)
            .padding(0)
            .style(theme::menu_item)
            .on_press(Message::MonitorTab(Tab::Disk))
            .into()
    } else {
        heading
    };
    if overview {
        return heading;
    }
    column![
        heading,
        button(label(app.name(cplus_core::Feature::Drive)).size(12))
            .padding([6, 12])
            .style(theme::secondary)
            .on_press(Message::MonitorCleanup)
    ]
    .spacing(6)
    .into()
}
fn network(app: &App) -> Element<'_, Message> {
    let net = app.monitor.sample.network.as_ref();
    card(
        column![
            row![
                bold(app.t("Network"), 15).width(Length::Fill),
                small(format!(
                    "↑ {}   ↓ {}",
                    speed(net.and_then(|n| n.upload)),
                    speed(net.and_then(|n| n.download))
                ))
            ],
            chart(
                app,
                Tab::Network,
                if app.monitor.tab == Tab::Network {
                    32.0
                } else {
                    48.0
                }
            ),
            small(format!(
                "{} · {}",
                net.map(|n| n.name.as_str()).unwrap_or("--"),
                app.t("Last minute")
            ))
        ]
        .spacing(8),
    )
}
fn process_list(app: &App, tab: Tab) -> Element<'_, Message> {
    let mut rows: Vec<_> = app.monitor.sample.processes.iter().collect();
    if tab == Tab::Cpu {
        rows.sort_by(|a, b| {
            b.cpu
                .unwrap_or(-1.0)
                .total_cmp(&a.cpu.unwrap_or(-1.0))
                .then(a.pid.cmp(&b.pid))
        });
    } else {
        rows.sort_by_key(|p| (std::cmp::Reverse(p.memory), p.pid));
    }
    let max = rows
        .first()
        .map(|p| {
            if tab == Tab::Cpu {
                p.cpu.unwrap_or(0.0)
            } else {
                p.memory.unwrap_or(0) as f64
            }
        })
        .unwrap_or(0.0)
        .max(1.0);
    let mut content = Column::new().spacing(4);
    for process in rows.into_iter().take(64) {
        let icon: Element<'_, Message> = app
            .monitor
            .icons
            .get(&process.path)
            .and_then(|i| i.as_ref())
            .map(|h| image(h.clone()).width(23).height(23).into())
            .unwrap_or_else(|| small("▣").size(22).into());
        let (number, value) = if tab == Tab::Cpu {
            (
                process.cpu.unwrap_or(0.0),
                process
                    .cpu
                    .map(|v| format!("{v:.1}%"))
                    .unwrap_or_else(|| "--".into()),
            )
        } else {
            (
                process.memory.unwrap_or(0) as f64,
                process.memory.map(bytes).unwrap_or_else(|| "--".into()),
            )
        };
        let background = row![
            container(widget::Space::new().height(36))
                .width(Length::FillPortion(((number / max * 100.0) as u16).max(1)))
                .style(|_| container::Style {
                    background: Some(theme::PALE.into()),
                    ..Default::default()
                }),
            widget::Space::new().width(Length::FillPortion(
                ((1.0 - number / max) * 100.0) as u16 + 1
            ))
        ];
        let foreground = row![
            icon,
            label(process.name.clone())
                .size(12)
                .wrapping(widget::text::Wrapping::None)
                .width(Length::Fill),
            label(value).size(12)
        ]
        .spacing(8)
        .padding([6, 7])
        .align_y(iced::Alignment::Center);
        content = content.push(widget::stack![background, foreground]);
    }
    if !app.monitor.sample.processes_available {
        content = content.push(small(app.t("Loading or unavailable")));
    }
    column![
        row![
            small(format!(
                "{} · {}",
                app.t("Application name"),
                app.t("Top 64")
            ))
            .width(Length::Fill),
            small(format!("{} ↓", app.t(tab.key())))
        ],
        scrollable(content).height(Length::Fill)
    ]
    .spacing(8)
    .into()
}
pub fn view(app: &App) -> Element<'_, Message> {
    let tab = app.monitor.tab;
    let sample = &app.monitor.sample;
    let mut tabs = Row::new().spacing(2);
    for item in Tab::ALL {
        tabs = tabs.push(
            button(label(app.t(item.key())).size(13).color(if item == tab {
                theme::MINT
            } else {
                theme::MUTED
            }))
            .padding([9, 6])
            .style(theme::menu_item)
            .on_press(Message::MonitorTab(item)),
        );
    }
    tabs = tabs.push(widget::Space::new().width(Length::Fill)).push(
        button(label("×").size(20))
            .style(theme::menu_item)
            .on_press(Message::MonitorClose),
    );
    let body: Element<'_, Message> = match tab {
        Tab::Overview => {
            let cpu = card(
                column![
                    row![
                        bold("CPU ›", 14).width(Length::Fill),
                        bold(percentage(sample.cpu), 20)
                    ],
                    chart(app, Tab::Cpu, 26.0),
                    row![
                        small(app.t("CPU temperature")).width(Length::Fill),
                        cpu_temperature(app)
                    ]
                ]
                .spacing(6),
            );
            let mem = meter(
                app,
                "Memory",
                sample.memory_percent(),
                small(format!(
                    "{} / {}",
                    bytes(sample.memory_used),
                    bytes(sample.memory_total)
                )),
            );
            column![
                button(cpu)
                    .padding(0)
                    .style(theme::menu_item)
                    .on_press(Message::MonitorTab(Tab::Cpu)),
                button(mem)
                    .padding(0)
                    .style(theme::menu_item)
                    .on_press(Message::MonitorTab(Tab::Memory)),
                disk(app, true),
                button(network(app))
                    .padding(0)
                    .style(theme::menu_item)
                    .on_press(Message::MonitorTab(Tab::Network))
            ]
            .spacing(6)
            .into()
        }
        Tab::Cpu => column![
            meter(
                app,
                "CPU usage",
                sample.cpu,
                row![
                    small(app.t("Overall system usage")).width(Length::Fill),
                    row![small(app.t("Temperature")), cpu_temperature(app)].spacing(4)
                ]
                .spacing(6)
            ),
            process_list(app, tab)
        ]
        .spacing(16)
        .into(),
        Tab::Memory => column![
            meter(
                app,
                "Memory",
                sample.memory_percent(),
                small(format!(
                    "{} {} / {}",
                    app.t("Used"),
                    bytes(sample.memory_used),
                    bytes(sample.memory_total)
                ))
            ),
            process_list(app, tab)
        ]
        .spacing(16)
        .into(),
        Tab::Disk => {
            let info = sample.disk.as_ref();
            column![
                disk(app, false),
                card(
                    column![
                        row![
                            bold(app.t("Disk activity"), 14).width(Length::Fill),
                            small(app.t("All disks"))
                        ],
                        row![
                            column![small(app.t("Read")), bold(speed(sample.read), 20)]
                                .width(Length::Fill),
                            column![small(app.t("Write")), bold(speed(sample.write), 20)]
                        ],
                        chart(app, Tab::Disk, 48.0),
                        small(app.t("Last minute"))
                    ]
                    .spacing(10)
                ),
                card(
                    column![
                        bold(app.t("Disk information"), 14),
                        pair(
                            app.t("System drive"),
                            info.map(|d| d.root.clone()).unwrap_or_else(|| "--".into())
                        ),
                        pair(
                            app.t("File system"),
                            info.map(|d| d.filesystem.clone())
                                .unwrap_or_else(|| "--".into())
                        ),
                        row![
                            small(app.t("Temperature")).width(Length::Fill),
                            temperature(app, sample.temperatures.disk)
                        ]
                    ]
                    .spacing(8)
                )
            ]
            .spacing(12)
            .into()
        }
        Tab::Network => {
            let n = sample.network.as_ref();
            column![
                network(app),
                container(
                    column![
                        bold(app.t("Traffic this session"), 14),
                        row![
                            bold(
                                format!("↓ {}", bytes(n.map(|n| n.received).unwrap_or(0))),
                                20
                            )
                            .width(Length::Fill),
                            bold(format!("↑ {}", bytes(n.map(|n| n.sent).unwrap_or(0))), 20)
                        ],
                        small(app.t("Counted while monitoring; no history saved"))
                    ]
                    .spacing(6)
                )
                .padding(10)
                .width(Length::Fill)
                .style(theme::card),
                container(
                    column![
                        bold(app.t("Network interface"), 14),
                        pair(
                            app.t("Interface"),
                            n.map(|n| n.name.clone()).unwrap_or_else(|| "--".into())
                        ),
                        pair(
                            app.t("WLAN name"),
                            n.map(|n| n.wlan_name.clone())
                                .filter(|s| !s.is_empty())
                                .unwrap_or_else(|| "--".into())
                        ),
                        pair(
                            app.t("Status"),
                            app.t(if n.is_some() {
                                "Connected"
                            } else {
                                "Unavailable"
                            })
                        ),
                        pair(
                            app.t("Link speed"),
                            n.map(|n| format!("{:.1} Mbps", n.link as f64 / 1_000_000.0))
                                .unwrap_or_else(|| "--".into())
                        ),
                        pair(
                            "IPv4".into(),
                            n.map(|n| n.ipv4.clone())
                                .filter(|s| !s.is_empty())
                                .unwrap_or_else(|| "--".into())
                        ),
                        pair(
                            "IPv6".into(),
                            n.map(|n| n.ipv6.clone())
                                .filter(|s| !s.is_empty())
                                .unwrap_or_else(|| "--".into())
                        )
                    ]
                    .spacing(6)
                )
                .padding(10)
                .width(Length::Fill)
                .style(theme::card)
            ]
            .spacing(8)
            .into()
        }
    };
    let warning = if app.monitor.stale() {
        app.t("Loading or unavailable")
    } else if !sample.error.is_empty() {
        app.t("Some counters are unavailable or warming up")
    } else {
        String::new()
    };
    let footer = row![
        button(small("⚙").size(19))
            .style(theme::menu_item)
            .on_press(Message::Dialog(crate::app::Dialog::Settings)),
        button(
            label(app.t("Open CPlus Cleaner"))
                .size(13)
                .color(theme::MINT)
        )
        .width(Length::Fill)
        .style(theme::menu_item)
        .on_press(Message::MonitorMain),
        button(small(app.t("Quit")))
            .style(theme::menu_item)
            .on_press(Message::MonitorClose)
    ]
    .align_y(iced::Alignment::Center);
    container(
        column![
            tabs,
            rule::horizontal(1),
            container(body).height(Length::Fill).padding([8, 0]),
            small(warning).size(10),
            rule::horizontal(1),
            footer
        ]
        .spacing(8),
    )
    .padding(12)
    .width(Length::Fill)
    .height(Length::Fill)
    .style(theme::surface)
    .into()
}
