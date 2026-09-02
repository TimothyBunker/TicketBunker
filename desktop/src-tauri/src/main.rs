// Keep the installed Windows application graphical: its core and agent
// helpers communicate through pipes owned by the app, not a console window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    ticketbunker_desktop_lib::run();
}
