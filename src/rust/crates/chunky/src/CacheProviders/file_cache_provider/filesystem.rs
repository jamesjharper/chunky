//! Filesystem primitives used by the cache provider and metadata store.

use super::{configuration::FileCacheProviderConfig, constants::is_safe_extension};
use crate::fingerprints::stable_hash_bytes;
use anyhow::{anyhow, Result};
use std::{
    collections::HashSet,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;
use walkdir::WalkDir;

#[derive(Clone)]
pub struct FileCacheProviderFilesystem {
    pub root: PathBuf,
    pub objects_root: PathBuf,
    pub config: Arc<FileCacheProviderConfig>,
}

impl FileCacheProviderFilesystem {
    pub fn new(root: PathBuf, objects_root: PathBuf, config: Arc<FileCacheProviderConfig>) -> Self {
        Self { root, objects_root, config }
    }

    pub fn new_generation_path(
        &self,
        fingerprint: &str,
        entry_id: &str,
        extension: &str,
    ) -> PathBuf {
        let key = stable_hash_bytes(fingerprint.as_bytes());
        let safe_extension = if is_safe_extension(extension) { extension } else { ".blob" };
        self.objects_root
            .join(&key[..2])
            .join(&key)
            .join(format!("{entry_id}{safe_extension}"))
    }

    pub fn relative_path(&self, path: &Path) -> Result<String> {
        if let Ok(relative) = path.strip_prefix(&self.root) {
            return Ok(relative.to_string_lossy().replace('\\', "/"));
        }
        let root = self.root.canonicalize().unwrap_or_else(|_| self.root.clone());
        let resolved = dunce::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        let relative = resolved.strip_prefix(&root).map_err(|_| {
            anyhow!("cache path escapes cache root: {}", path.display())
        })?;
        Ok(relative.to_string_lossy().replace('\\', "/"))
    }

    pub fn new_temporary_path(final_path: &Path) -> PathBuf {
        let name = final_path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("cache-object");
        final_path.with_file_name(format!(".{name}.{}.tmp", Uuid::new_v4().simple()))
    }

    pub fn write_temporary_file(&self, temporary_path: &Path, raw: &[u8]) -> Result<(u64, String)> {
        if let Some(parent) = temporary_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut stream = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(temporary_path)?;
        if let Err(error) = (|| -> Result<()> {
            stream.write_all(raw)?;
            stream.flush()?;
            if self.config.fsync_data {
                stream.sync_data()?;
            }
            Ok(())
        })() {
            let _ = fs::remove_file(temporary_path);
            return Err(error);
        }
        Ok((raw.len() as u64, stable_hash_bytes(raw)))
    }

    pub fn fsync_directory(directory: &Path) -> Result<()> {
        let file = File::open(directory)?;
        file.sync_all()?;
        Ok(())
    }

    pub fn remove_paths(&self, paths: impl IntoIterator<Item = PathBuf>) -> Result<usize> {
        let mut removed = 0;
        let mut seen = HashSet::new();
        for path in paths {
            if !seen.insert(path.clone()) {
                continue;
            }
            match fs::remove_file(&path) {
                Ok(()) => removed += 1,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) if error.kind() == std::io::ErrorKind::IsADirectory => {}
                Err(error) => return Err(error.into()),
            }
        }
        Ok(removed)
    }

    pub fn cleanup_orphan_files(
        &self,
        referenced_paths: &HashSet<String>,
        grace_seconds: f64,
    ) -> Result<usize> {
        if !self.objects_root.exists() {
            return Ok(0);
        }
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs_f64();
        let mut removed = 0;
        for entry in WalkDir::new(&self.objects_root).into_iter().filter_map(Result::ok) {
            if !entry.file_type().is_file() {
                continue;
            }
            let path = entry.path();
            let relative = self.relative_path(path)?;
            if referenced_paths.contains(&relative) {
                continue;
            }
            let modified = entry
                .metadata()?
                .modified()?
                .duration_since(UNIX_EPOCH)?
                .as_secs_f64();
            if now - modified < grace_seconds {
                continue;
            }
            match fs::remove_file(path) {
                Ok(()) => removed += 1,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        self.remove_empty_object_directories()?;
        Ok(removed)
    }

    pub fn remove_empty_object_directories(&self) -> Result<()> {
        let mut directories: Vec<PathBuf> = WalkDir::new(&self.objects_root)
            .into_iter()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_type().is_dir())
            .map(|entry| entry.path().to_path_buf())
            .collect();
        directories.sort_by_key(|path| std::cmp::Reverse(path.components().count()));
        for directory in directories {
            let _ = fs::remove_dir(directory);
        }
        Ok(())
    }
}
