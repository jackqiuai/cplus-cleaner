use super::*;

fn put32(bytes: &mut [u8], offset: usize, value: u32) {
    bytes[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}
fn put64(bytes: &mut [u8], offset: usize, value: u64) {
    bytes[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
}

fn record(id: u64, parent: u64, name: &str, directory: bool, version: u32) -> Vec<u8> {
    let name = name.encode_utf16().collect::<Vec<_>>();
    let first = size_of::<FILE_LAYOUT_ENTRY>();
    let stream =
        (first + offset_of!(FILE_LAYOUT_NAME_ENTRY, FileName) + name.len() * 2).next_multiple_of(8);
    let mut bytes = vec![
        0_u8;
        stream
            + if directory {
                0
            } else {
                offset_of!(STREAM_LAYOUT_ENTRY, StreamIdentifier)
            }
    ];
    put32(&mut bytes, offset_of!(FILE_LAYOUT_ENTRY, Version), 1);
    put32(
        &mut bytes,
        offset_of!(FILE_LAYOUT_ENTRY, FileAttributes),
        if directory {
            FILE_ATTRIBUTE_DIRECTORY
        } else {
            0
        },
    );
    put64(
        &mut bytes,
        offset_of!(FILE_LAYOUT_ENTRY, FileReferenceNumber),
        id,
    );
    put32(
        &mut bytes,
        offset_of!(FILE_LAYOUT_ENTRY, FirstNameOffset),
        first as u32,
    );
    put32(
        &mut bytes,
        first + offset_of!(FILE_LAYOUT_NAME_ENTRY, Flags),
        FILE_LAYOUT_NAME_ENTRY_PRIMARY,
    );
    put64(
        &mut bytes,
        first + offset_of!(FILE_LAYOUT_NAME_ENTRY, ParentFileReferenceNumber),
        parent,
    );
    put32(
        &mut bytes,
        first + offset_of!(FILE_LAYOUT_NAME_ENTRY, FileNameLength),
        (name.len() * 2) as u32,
    );
    for (index, unit) in name.iter().enumerate() {
        let at = first + offset_of!(FILE_LAYOUT_NAME_ENTRY, FileName) + index * 2;
        bytes[at..at + 2].copy_from_slice(&unit.to_le_bytes());
    }
    if !directory {
        put32(
            &mut bytes,
            offset_of!(FILE_LAYOUT_ENTRY, FirstStreamOffset),
            stream as u32,
        );
        put32(
            &mut bytes,
            stream + offset_of!(STREAM_LAYOUT_ENTRY, Version),
            version,
        );
        put32(
            &mut bytes,
            stream + offset_of!(STREAM_LAYOUT_ENTRY, AttributeTypeCode),
            0x80,
        );
        put64(
            &mut bytes,
            stream + offset_of!(STREAM_LAYOUT_ENTRY, EndOfFile),
            64 * 1024 * 1024,
        );
    }
    bytes
}

fn page(mut records: Vec<Vec<u8>>) -> Vec<u8> {
    let mut bytes = vec![0; size_of::<QUERY_FILE_LAYOUT_OUTPUT>()];
    put32(
        &mut bytes,
        offset_of!(QUERY_FILE_LAYOUT_OUTPUT, FileEntryCount),
        records.len() as u32,
    );
    put32(
        &mut bytes,
        offset_of!(QUERY_FILE_LAYOUT_OUTPUT, FirstFileOffset),
        size_of::<QUERY_FILE_LAYOUT_OUTPUT>() as u32,
    );
    let count = records.len();
    for (index, record) in records.iter_mut().enumerate() {
        let length = record.len() as u32;
        if index + 1 < count {
            put32(
                record,
                offset_of!(FILE_LAYOUT_ENTRY, NextFileOffset),
                length,
            );
        }
        bytes.extend_from_slice(record);
    }
    bytes
}

#[test]
fn both_sdk_stream_versions_resolve_unordered_unicode_paths() {
    let mut index = Index::default();
    index
        .page(
            &page(vec![
                record(80, 30, "电影.pdf", false, 1),
                record(81, 30, "sparse.bin", false, 2),
                record(30, 5, "资料", true, 0),
            ]),
            5,
            30 * 1024 * 1024,
            &|| false,
        )
        .unwrap();
    let paths = index
        .paths(Path::new(r"C:\"), 5, &|| false, &|_| false)
        .unwrap();
    assert_eq!(
        paths,
        vec![
            (PathBuf::from(r"C:\资料\电影.pdf"), 80),
            (PathBuf::from(r"C:\资料\sparse.bin"), 81)
        ]
    );
    assert!(
        index
            .paths(Path::new(r"C:\"), 5, &|| false, &|p| p.ends_with("资料"))
            .unwrap()
            .is_empty()
    );
}

#[test]
fn invalid_offsets_names_versions_and_signed_lengths_fail_closed() {
    let make = || record(80, 5, "large.bin", false, 1);
    let mut cases = Vec::new();
    let mut bad = make();
    put32(&mut bad, offset_of!(FILE_LAYOUT_ENTRY, FirstNameOffset), 1);
    cases.push(bad);
    let mut bad = make();
    put32(
        &mut bad,
        size_of::<FILE_LAYOUT_ENTRY>() + offset_of!(FILE_LAYOUT_NAME_ENTRY, FileNameLength),
        3,
    );
    cases.push(bad);
    let mut bad = make();
    let at = uint32(&bad, offset_of!(FILE_LAYOUT_ENTRY, FirstStreamOffset)).unwrap() as usize;
    put64(
        &mut bad,
        at + offset_of!(STREAM_LAYOUT_ENTRY, EndOfFile),
        u64::MAX,
    );
    cases.push(bad);
    let mut bad = make();
    let at = uint32(&bad, offset_of!(FILE_LAYOUT_ENTRY, FirstStreamOffset)).unwrap() as usize;
    put32(
        &mut bad,
        at + offset_of!(STREAM_LAYOUT_ENTRY, NextStreamOffset),
        1,
    );
    cases.push(bad);
    cases.push(record(80, 5, r"..\outside.bin", false, 1));
    cases.push(record(80, 5, "large.bin", false, 99));
    for bad in cases {
        assert!(
            Index::default()
                .page(&page(vec![bad]), 5, 1, &|| false)
                .is_err()
        );
    }
    let mut bad = page(vec![make(), make()]);
    put32(
        &mut bad,
        size_of::<QUERY_FILE_LAYOUT_OUTPUT>() + offset_of!(FILE_LAYOUT_ENTRY, NextFileOffset),
        1,
    );
    assert!(Index::default().page(&bad, 5, 1, &|| false).is_err());
}

#[test]
fn unknown_or_cyclic_parents_trigger_fallback_and_links_are_pruned() {
    let mut missing = Index::default();
    missing
        .page(
            &page(vec![record(80, 30, "large.bin", false, 1)]),
            5,
            1,
            &|| false,
        )
        .unwrap();
    assert!(
        missing
            .paths(Path::new(r"C:\"), 5, &|| false, &|_| false)
            .is_err()
    );
    let mut cycle = Index::default();
    cycle
        .page(
            &page(vec![
                record(30, 31, "a", true, 0),
                record(31, 30, "b", true, 0),
                record(80, 30, "large.bin", false, 1),
            ]),
            5,
            1,
            &|| false,
        )
        .unwrap();
    assert!(
        cycle
            .paths(Path::new(r"C:\"), 5, &|| false, &|_| false)
            .is_err()
    );
    let mut linked = Index::default();
    let mut dir = record(30, 5, "link", true, 0);
    put32(
        &mut dir,
        offset_of!(FILE_LAYOUT_ENTRY, FileAttributes),
        FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT,
    );
    linked
        .page(
            &page(vec![dir, record(80, 30, "large.bin", false, 1)]),
            5,
            1,
            &|| false,
        )
        .unwrap();
    assert!(
        linked
            .paths(Path::new(r"C:\"), 5, &|| false, &|_| false)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn cancelled_or_malformed_pages_cannot_publish_candidates() {
    assert_eq!(
        Index::default()
            .page(
                &page(vec![record(80, 5, "large.bin", false, 1)]),
                5,
                1,
                &|| true
            )
            .unwrap_err()
            .kind(),
        io::ErrorKind::Interrupted
    );
    let mut seed = 17_u32;
    for length in 0..512 {
        let bytes = (0..length)
            .map(|_| {
                seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
                (seed >> 24) as u8
            })
            .collect::<Vec<_>>();
        assert!(Index::default().page(&bytes, 5, 1, &|| false).is_err());
    }
}

#[test]
fn pathological_component_and_path_lengths_trigger_directory_fallback() {
    let long_name = "x".repeat(256);
    assert!(
        Index::default()
            .page(
                &page(vec![record(80, 5, &long_name, false, 1)]),
                5,
                1,
                &|| false
            )
            .is_err()
    );
    let mut index = Index::default();
    let mut parent = 5;
    for id in 100..300 {
        index.directories.insert(
            id,
            Directory {
                name: Name {
                    parent,
                    value: OsString::from("x".repeat(200)),
                },
                boundary: false,
            },
        );
        parent = id;
    }
    index.files.push((
        500,
        Name {
            parent,
            value: OsString::from("large.bin"),
        },
    ));
    assert!(
        index
            .paths(Path::new(r"C:\"), 5, &|| false, &|_| false)
            .is_err()
    );
}
