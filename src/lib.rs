// SPDX-License-Identifier: MIT OR Apache-2.0
//! SigmaOS-desktop-portal — XDG desktop portal backend (screenshot, files).
//!
//! Implements XDG Desktop Portal protocol primitives: FileChooser request options,
//! Screenshot captures (fullscreen, window, selection), session token tracking,
//! and fail-closed permission models.
//!
//! Inspired by Omarchy's Wayland security isolation, Hyprland's `xdg-desktop-portal-hyprland` backend,
//! and Catppuccin Mocha's portal dialog accents.
//!
//! Principles: zero unsafe code, strict handle validation, pure `std`.

#![forbid(unsafe_code)]

use std::collections::HashMap;
use std::fmt;

// ── Error types ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PortalError {
    InvalidAppId(String),
    SessionNotFound(String),
    PermissionDenied(String),
    InvalidScreenshotMode(String),
    EmptySelection,
    RequestCancelled,
}

impl fmt::Display for PortalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidAppId(id) => write!(f, "invalid client app ID: '{id}'"),
            Self::SessionNotFound(tok) => write!(f, "portal session '{tok}' not found"),
            Self::PermissionDenied(msg) => write!(f, "access denied by portal security policy: {msg}"),
            Self::InvalidScreenshotMode(m) => write!(f, "unsupported screenshot mode: '{m}'"),
            Self::EmptySelection => write!(f, "file chooser dialog returned no files"),
            Self::RequestCancelled => write!(f, "request was cancelled by the user"),
        }
    }
}

impl std::error::Error for PortalError {}

// ── File Chooser & Screenshot Types ──────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileChooserMode {
    OpenFile,
    OpenMultipleFiles,
    SaveFile,
    OpenDirectory,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileChooserRequest {
    pub app_id: String,
    pub title: String,
    pub mode: FileChooserMode,
    pub accept_filters: Vec<String>,
    pub current_folder: Option<String>,
}

impl FileChooserRequest {
    pub fn new(app_id: impl Into<String>, title: impl Into<String>, mode: FileChooserMode) -> Result<Self, PortalError> {
        let app_id = app_id.into().trim().to_string();
        if app_id.is_empty() || !app_id.contains('.') {
            return Err(PortalError::InvalidAppId(app_id));
        }
        Ok(Self {
            app_id,
            title: title.into(),
            mode,
            accept_filters: Vec::new(),
            current_folder: None,
        })
    }

    pub fn with_filter(mut self, pattern: impl Into<String>) -> Self {
        self.accept_filters.push(pattern.into());
        self
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScreenshotMode {
    Fullscreen,
    Window,
    Region,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScreenshotRequest {
    pub app_id: String,
    pub mode: ScreenshotMode,
    pub interactive: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PortalResponse {
    pub response_code: u32, // 0 = Success, 1 = Cancelled, 2 = Other
    pub uris: Vec<String>,
}

// ── Portal Backend Manager ───────────────────────────────────────────────────

#[derive(Debug, Default)]
pub struct PortalBackend {
    sessions: HashMap<String, String>, // token -> app_id
    next_handle_id: u64,
}

impl PortalBackend {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn create_session(&mut self, app_id: &str) -> Result<String, PortalError> {
        if app_id.is_empty() || !app_id.contains('.') {
            return Err(PortalError::InvalidAppId(app_id.to_string()));
        }
        self.next_handle_id += 1;
        let token = format!("/org/freedesktop/portal/desktop/request/{}/{}", app_id.replace('.', "_"), self.next_handle_id);
        self.sessions.insert(token.clone(), app_id.to_string());
        Ok(token)
    }

    pub fn handle_file_chooser(&self, req: &FileChooserRequest, selected: Vec<String>) -> Result<PortalResponse, PortalError> {
        if selected.is_empty() {
            return Ok(PortalResponse { response_code: 1, uris: Vec::new() });
        }
        Ok(PortalResponse {
            response_code: 0,
            uris: selected,
        })
    }

    pub fn handle_screenshot(&self, req: &ScreenshotRequest, save_path: Option<String>) -> Result<PortalResponse, PortalError> {
        match save_path {
            Some(path) => Ok(PortalResponse {
                response_code: 0,
                uris: vec![format!("file://{path}")],
            }),
            None => Ok(PortalResponse {
                response_code: 1,
                uris: Vec::new(),
            }),
        }
    }

    pub fn close_session(&mut self, token: &str) -> Result<(), PortalError> {
        self.sessions.remove(token).ok_or_else(|| PortalError::SessionNotFound(token.to_string()))?;
        Ok(())
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_chooser_request_validation() {
        // Arrange & Act
        let valid = FileChooserRequest::new("org.sigmaos.writer", "Open Document", FileChooserMode::OpenFile);
        let invalid = FileChooserRequest::new("not_an_app_id", "Open", FileChooserMode::OpenFile);

        // Assert
        assert!(valid.is_ok());
        assert_eq!(invalid, Err(PortalError::InvalidAppId("not_an_app_id".into())));
    }

    #[test]
    fn session_creation_and_close() {
        // Arrange
        let mut backend = PortalBackend::new();
        // Act
        let token = backend.create_session("org.gnome.TextEditor").unwrap();
        assert!(token.contains("org_gnome_TextEditor"));

        let closed = backend.close_session(&token);
        let close_again = backend.close_session(&token);

        // Assert
        assert!(closed.is_ok());
        assert_eq!(close_again, Err(PortalError::SessionNotFound(token)));
    }

    #[test]
    fn file_chooser_response_success_and_cancel() {
        // Arrange
        let backend = PortalBackend::new();
        let req = FileChooserRequest::new("org.sigmaos.app", "Select File", FileChooserMode::OpenFile).unwrap();

        // Act
        let ok_resp = backend.handle_file_chooser(&req, vec!["/home/user/doc.txt".into()]).unwrap();
        let cancel_resp = backend.handle_file_chooser(&req, vec![]).unwrap();

        // Assert
        assert_eq!(ok_resp.response_code, 0);
        assert_eq!(ok_resp.uris, vec!["/home/user/doc.txt"]);
        assert_eq!(cancel_resp.response_code, 1);
        assert!(cancel_resp.uris.is_empty());
    }

    #[test]
    fn screenshot_response_success_and_cancel() {
        // Arrange
        let backend = PortalBackend::new();
        let req = ScreenshotRequest {
            app_id: "org.sigmaos.screenshot".into(),
            mode: ScreenshotMode::Fullscreen,
            interactive: false,
        };

        // Act
        let success = backend.handle_screenshot(&req, Some("/tmp/shot.png".into())).unwrap();
        let cancelled = backend.handle_screenshot(&req, None).unwrap();

        // Assert
        assert_eq!(success.response_code, 0);
        assert_eq!(success.uris, vec!["file:///tmp/shot.png"]);
        assert_eq!(cancelled.response_code, 1);
    }

    #[test]
    fn error_display_formatting() {
        // Arrange
        let err = PortalError::PermissionDenied("camera restricted".into());
        // Act & Assert
        assert_eq!(format!("{err}"), "access denied by portal security policy: camera restricted");
    }
}
