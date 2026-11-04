fn main() {
    println!("cargo:rerun-if-changed=../../resources/assets/tray.ico");
    println!("cargo:rerun-if-changed=app.manifest");
    let mut resource = winresource::WindowsResource::new();
    resource.set_icon("../../resources/assets/tray.ico");
    resource.set_manifest(include_str!("app.manifest"));
    resource.set("ProductName", "CPlus Cleaner");
    resource.set("FileDescription", "CPlus Cleaner - Drive Cleanup Assistant");
    resource.set("LegalCopyright", "Copyright (c) 2026 jackqiuai");
    resource.compile().expect("Windows application resources");
}
