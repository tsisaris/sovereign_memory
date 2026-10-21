"""Letta's git-backed memory repository: block files, history, local storage and skills.

This is a port of the open-source memory-repository code of Letta (letta-ai/letta commit
56ba9c25, Apache-2.0): letta/services/memory_repo/git_operations.py, memfs_client_base.py,
block_markdown.py, path_mapping.py, storage/base.py and storage/local.py, and the two schemas of
letta/schemas/memory_repo.py. Each agent's memory blocks are Markdown files (a YAML frontmatter
with description, read_only and metadata, then the block's text) in one git repository per agent,
kept under a base folder on the local disk. Every change is a commit, so the history of every
block is the repository's own log. A file skills/{name}/SKILL.md is the block labelled
skills/{name}; other markdown under skills/ is ignored.

Changes from the original, all because of where it runs: the Redis lock around a commit is an
in-process lock per agent, the organization and author come in as plain ids instead of a User
object, and logging is the standard logger. It never calls a model and never writes outside the
base folder it is given.
"""

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import Field

from letta_memory_model.constants import CORE_MEMORY_BLOCK_CHAR_LIMIT
from letta_memory_model.schemas.block import BaseBlock
from letta_memory_model.schemas.block import Block as PydanticBlock
from letta_memory_model.schemas.letta_base import LettaBase

logger = logging.getLogger(__name__)


class MemoryCommit(LettaBase):
    """One commit in an agent's memory repository: who, when, why and which files."""

    __id_prefix__ = "memcommit"

    sha: str = Field(..., description="Commit SHA (40-char hex).")
    parent_sha: Optional[str] = Field(None, description="Parent commit SHA.")
    message: str = Field(..., description="Commit message.")
    author_type: str = Field(..., description="Author type: agent, user, system.")
    author_id: str = Field(..., description="Author ID.")
    author_name: Optional[str] = Field(None, description="Human-readable author name.")
    timestamp: datetime = Field(..., description="Commit timestamp.")
    files_changed: List[str] = Field(default_factory=list, description="Changed file paths.")
    additions: int = Field(default=0, description="Number of characters added.")
    deletions: int = Field(default=0, description="Number of characters deleted.")


class FileChange(LettaBase):
    """One file change to commit: a path, the new content (None deletes) and the change kind."""

    path: str = Field(..., description="File path within the repository.")
    content: Optional[str] = Field(None, description="New file content (None for delete).")
    change_type: str = Field(default="modify", description="Change type: add, modify, delete.")


# Storage -------------------------------------------------------------------------------------


class StorageBackend(ABC):
    """Where a memory repository's git files are kept: upload, download, list and delete by path.

    A subclass takes a base location; every method takes paths inside it. It never interprets
    what the files are.
    """

    @property
    @abstractmethod
    def bucket_name(self) -> str:
        """Return the bucket or container name."""

    @abstractmethod
    async def upload_bytes(self, path: str, content: bytes) -> None:
        """Store the bytes at the path, replacing what was there."""

    @abstractmethod
    async def download_bytes(self, path: str) -> bytes:
        """Return the bytes at the path or raise FileNotFoundError."""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Say whether anything is stored at the path."""

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete the file at the path or raise FileNotFoundError."""

    @abstractmethod
    async def list_files(self, prefix: str) -> List[str]:
        """Return every stored file path that starts with the prefix."""

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """Delete every file under the prefix and return how many were deleted."""

    async def upload_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Store text at the path, encoded as asked."""
        await self.upload_bytes(path, content.encode(encoding))

    async def download_text(self, path: str, encoding: str = "utf-8") -> str:
        """Return the text stored at the path, decoded as asked."""
        return (await self.download_bytes(path)).decode(encoding)

    async def copy(self, source_path: str, dest_path: str) -> None:
        """Copy a stored file to another path."""
        await self.upload_bytes(dest_path, await self.download_bytes(source_path))


class LocalStorageBackend(StorageBackend):
    """Keep a memory repository's git files on the local disk under a base folder.

    It takes the base folder (required here: the original's default was a folder in the user's
    home, and this port writes only where it is told) and a prefix. Files live at
    {base}/{prefix}/{path}. It never reads or writes outside that folder.
    """

    def __init__(self, base_path: str, prefix: str = "repository"):
        """Create the base folder if needed and remember the prefix."""
        self._base_path = Path(base_path)
        self._prefix = prefix.rstrip("/")
        self._bucket_name = "local"
        self._base_path.mkdir(parents=True, exist_ok=True)

    def _full_path(self, path: str) -> Path:
        """Return the filesystem path for a storage path, refusing one that leaves the folder."""
        path = path.lstrip("/")
        root = (self._base_path / self._prefix) if self._prefix else self._base_path
        full = root / path
        if root.resolve() not in full.resolve().parents and full.resolve() != root.resolve():
            raise ValueError(f"path {path!r} leaves the storage folder")
        return full

    @property
    def bucket_name(self) -> str:
        """Return the bucket name, kept for interface compatibility."""
        return self._bucket_name

    async def upload_bytes(self, path: str, content: bytes) -> None:
        """Write bytes to a local file, making folders as needed."""
        full_path = self._full_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    async def download_bytes(self, path: str) -> bytes:
        """Read bytes from a local file or raise FileNotFoundError."""
        full_path = self._full_path(path)
        if not full_path.exists():
            raise FileNotFoundError(f"{full_path} not found")
        return full_path.read_bytes()

    async def exists(self, path: str) -> bool:
        """Say whether the local path exists."""
        return self._full_path(path).exists()

    async def delete(self, path: str) -> None:
        """Delete a local file or raise FileNotFoundError."""
        full_path = self._full_path(path)
        if not full_path.exists():
            raise FileNotFoundError(f"{full_path} not found")
        full_path.unlink()

    async def list_files(self, prefix: str) -> List[str]:
        """List files under the prefix as paths relative to {base}/{prefix}."""
        full_prefix = self._full_path(prefix)
        if not full_prefix.exists():
            return []
        root = self._base_path / self._prefix
        if full_prefix.is_file():
            return [str(full_prefix.relative_to(root))]
        return [str(p.relative_to(root)) for p in full_prefix.rglob("*") if p.is_file()]

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every file under the prefix and return the count."""
        full_prefix = self._full_path(prefix)
        if not full_prefix.exists():
            return 0
        if full_prefix.is_file():
            full_prefix.unlink()
            return 1
        count = sum(1 for p in full_prefix.rglob("*") if p.is_file())
        shutil.rmtree(full_prefix, ignore_errors=True)
        return count

    async def copy(self, source_path: str, dest_path: str) -> None:
        """Copy a local file, keeping its times; raise FileNotFoundError if it is missing."""
        source_full = self._full_path(source_path)
        dest_full = self._full_path(dest_path)
        if not source_full.exists():
            raise FileNotFoundError(f"{source_full} not found")
        dest_full.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_full, dest_full)


# Markdown block files ---------------------------------------------------------------------


def _field_default(field_name: str) -> Any:
    """Return the default of a block field."""
    return BaseBlock.model_fields[field_name].default


def serialize_block(
    value: str,
    *,
    description: Optional[str] = None,
    limit: Optional[int] = None,
    read_only: bool = False,
    metadata: Optional[dict] = None,
) -> str:
    """Write a block as Markdown: a YAML frontmatter, then the text.

    Goes in: the text and the block's description, read_only flag and metadata; limit is accepted
    and left out on purpose (deprecated for git memory). Comes out: the file text. The
    description is always written; read_only and metadata only when they differ from the default.
    It never writes a file.
    """
    front: Dict[str, Any] = {"description": description}
    if read_only != _field_default("read_only"):
        front["read_only"] = read_only
    if metadata and metadata != _field_default("metadata"):
        front["metadata"] = metadata
    yaml_str = yaml.dump(
        front, default_flow_style=False, sort_keys=False, allow_unicode=True
    ).rstrip("\n")
    return f"---\n{yaml_str}\n---\n{value}"


def _extract_frontmatter(content: str) -> tuple[Optional[str], str]:
    """Split a file into its frontmatter text and body; (None, content) when it has none."""
    if not content.startswith("---\n"):
        return None, content
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return None, content
    return content[4:end_idx], content[end_idx + 5 :]


def merge_frontmatter_with_body(
    existing_content: str,
    *,
    value: str,
    description: Optional[str],
    limit: Optional[int],
    read_only: bool,
    metadata: Optional[dict],
) -> str:
    """Update a block file's text while keeping its existing frontmatter formatting when possible.

    Goes in: the file as it is, and the wanted text, description, read_only and metadata. Comes
    out: the new file text. Frontmatter whose meaning is unchanged is kept character for
    character; otherwise it is rewritten in the normal form; a file with no frontmatter gets one.
    """
    yaml_str, _body = _extract_frontmatter(existing_content)
    if yaml_str is None:
        return serialize_block(
            value=value,
            description=description,
            limit=limit,
            read_only=read_only,
            metadata=metadata,
        )
    try:
        parsed = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    desired_metadata = metadata if metadata is not None else _field_default("metadata")
    changed = False
    if "description" not in parsed or parsed.get("description") != description:
        parsed["description"] = description
        changed = True
    if "limit" in parsed:
        del parsed["limit"]
        changed = True
    if read_only != _field_default("read_only"):
        if parsed.get("read_only") != read_only:
            parsed["read_only"] = read_only
            changed = True
    elif "read_only" in parsed:
        del parsed["read_only"]
        changed = True
    if desired_metadata and desired_metadata != _field_default("metadata"):
        if parsed.get("metadata") != desired_metadata:
            parsed["metadata"] = desired_metadata
            changed = True
    elif "metadata" in parsed:
        del parsed["metadata"]
        changed = True
    if not changed:
        return f"---\n{yaml_str}\n---\n{value}"
    normalized = yaml.dump(
        parsed, default_flow_style=False, sort_keys=False, allow_unicode=True
    ).rstrip("\n")
    return f"---\n{normalized}\n---\n{value}"


def parse_block_markdown(content: str) -> Dict[str, Any]:
    """Read a block file into its fields.

    Goes in: the file text; comes out: a dictionary with "value" (the body) and, when the
    frontmatter has them, "description", "limit", "read_only" and "metadata". A file with no
    frontmatter, an unclosed one, malformed YAML or YAML that is not a map is all value.
    """
    if not content.startswith("---\n"):
        return {"value": content}
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return {"value": content}
    try:
        front = yaml.safe_load(content[4:end_idx])
    except yaml.YAMLError:
        return {"value": content}
    if not isinstance(front, dict):
        return {"value": content}
    result: Dict[str, Any] = {"value": content[end_idx + 5 :]}
    for key in ("description", "limit", "read_only", "metadata"):
        if key in front:
            result[key] = front[key]
    return result


def memory_block_label_from_markdown_path(path: str) -> str | None:
    """Return the block label for a markdown path that syncs as a block, or None.

    Non-.md files are ignored; skills/{skill_name}/SKILL.md is the label skills/{skill_name};
    any other markdown under skills/ is ignored; every other .md file is its path without .md.
    """
    if not path.endswith(".md"):
        return None
    if path.startswith("skills/"):
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "skills" and parts[1] and parts[2] == "SKILL.md":
            return f"skills/{parts[1]}"
        return None
    return path[:-3]


# Git -----------------------------------------------------------------------------------------


def _run_git(args: List[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run one git command in a folder; raise CalledProcessError on failure when check is set."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, ["git", *args], result.stdout, result.stderr
        )
    return result


class GitOperations:
    """High-level git operations on agents' memory repositories kept in a storage backend.

    Each operation copies the agent's repository to a temporary folder, runs git there, and for a
    change copies back what changed. Commits to one agent's repository are serialised by an
    in-process lock. It needs the git program on the path and never touches any other
    repository.
    """

    def __init__(self, storage: StorageBackend):
        """Remember the storage backend; no repository is touched yet."""
        self.storage = storage
        self._git_available: Optional[bool] = None
        self._locks: Dict[str, asyncio.Lock] = {}

    def _check_git(self) -> None:
        """Raise RuntimeError unless the git program is available."""
        if self._git_available is None:
            try:
                subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
                self._git_available = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                self._git_available = False
        if not self._git_available:
            raise RuntimeError("git CLI is required for git operations but was not found in PATH")

    @staticmethod
    def _repo_path(agent_id: str, org_id: str) -> str:
        """Return the storage path of an agent's repository."""
        return f"{org_id}/{agent_id}/repo.git"

    async def create_repo(
        self,
        agent_id: str,
        org_id: str,
        initial_files: Optional[Dict[str, str]] = None,
        author_name: str = "Letta System",
        author_email: str = "system@letta.ai",
    ) -> str:
        """Create an agent's repository with an initial commit and return that commit's SHA.

        With no initial files it commits a .letta/config.json holding {"version": 1}.
        """
        self._check_git()

        def _create() -> tuple[str, str]:
            temp_dir = tempfile.mkdtemp(prefix="letta-memrepo-")
            try:
                repo_path = os.path.join(temp_dir, "repo")
                os.makedirs(repo_path)
                _run_git(["init", "-b", "main"], cwd=repo_path)
                _run_git(["config", "user.name", author_name], cwd=repo_path)
                _run_git(["config", "user.email", author_email], cwd=repo_path)
                if initial_files:
                    for file_path, content in initial_files.items():
                        full_path = os.path.join(repo_path, file_path)
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        with open(full_path, "w", encoding="utf-8") as handle:
                            handle.write(content)
                        _run_git(["add", file_path], cwd=repo_path)
                else:
                    letta_dir = os.path.join(repo_path, ".letta")
                    os.makedirs(letta_dir, exist_ok=True)
                    with open(os.path.join(letta_dir, "config.json"), "w") as handle:
                        handle.write('{"version": 1}')
                    _run_git(["add", ".letta/config.json"], cwd=repo_path)
                _run_git(["commit", "-m", "Initial commit"], cwd=repo_path)
                return repo_path, _run_git(["rev-parse", "HEAD"], cwd=repo_path).stdout.strip()
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise

        repo_path, commit_sha = await asyncio.to_thread(_create)
        try:
            await self._upload_repo(repo_path, agent_id, org_id)
            return commit_sha
        finally:
            shutil.rmtree(os.path.dirname(repo_path), ignore_errors=True)

    async def _upload_repo(self, local_repo_path: str, agent_id: str, org_id: str) -> None:
        """Copy every file of a local repository's .git folder into storage."""
        storage_prefix = self._repo_path(agent_id, org_id)
        git_dir = os.path.join(local_repo_path, ".git")
        uploads = []
        for root, _dirs, files in os.walk(git_dir):
            for filename in files:
                local_path = os.path.join(root, filename)
                rel_path = os.path.relpath(local_path, git_dir)
                with open(local_path, "rb") as handle:
                    uploads.append((f"{storage_prefix}/{rel_path}", handle.read()))
        await asyncio.gather(*[self.storage.upload_bytes(p, c) for p, c in uploads])

    @staticmethod
    def _snapshot_git_files(git_dir: str) -> Dict[str, float]:
        """Record the modification time of every file under .git, to find what a commit changed."""
        snapshot = {}
        for root, _dirs, files in os.walk(git_dir):
            for filename in files:
                path = os.path.join(root, filename)
                snapshot[path] = os.path.getmtime(path)
        return snapshot

    async def _upload_delta(
        self,
        local_repo_path: str,
        agent_id: str,
        org_id: str,
        before_snapshot: Dict[str, float],
    ) -> None:
        """Copy to storage only the .git files that are new or changed since the snapshot."""
        storage_prefix = self._repo_path(agent_id, org_id)
        git_dir = os.path.join(local_repo_path, ".git")
        uploads = []
        for root, _dirs, files in os.walk(git_dir):
            for filename in files:
                local_path = os.path.join(root, filename)
                old_mtime = before_snapshot.get(local_path)
                if old_mtime is None or os.path.getmtime(local_path) != old_mtime:
                    rel_path = os.path.relpath(local_path, git_dir)
                    with open(local_path, "rb") as handle:
                        uploads.append((f"{storage_prefix}/{rel_path}", handle.read()))
        await asyncio.gather(*[self.storage.upload_bytes(p, c) for p, c in uploads])

    async def _download_repo(self, agent_id: str, org_id: str) -> str:
        """Copy an agent's repository from storage to a temporary folder and return its path.

        Raises FileNotFoundError when the agent has no repository.
        """
        storage_prefix = self._repo_path(agent_id, org_id)
        files = await self.storage.list_files(storage_prefix)
        if not files:
            raise FileNotFoundError(f"No repository found for agent {agent_id}")
        temp_dir = tempfile.mkdtemp(prefix="letta-memrepo-")
        repo_path = os.path.join(temp_dir, "repo")
        git_dir = os.path.join(repo_path, ".git")
        os.makedirs(git_dir)
        file_info = []
        for file_path in files:
            if file_path.startswith(storage_prefix):
                rel_path = file_path[len(storage_prefix) + 1 :]
            else:
                rel_path = file_path.split("/")[-1] if "/" in file_path else file_path
            local_path = os.path.join(git_dir, rel_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            file_info.append((file_path, local_path))
        contents = await asyncio.gather(*[self.storage.download_bytes(fp) for fp, _ in file_info])
        for (_, local_path), content in zip(file_info, contents, strict=True):
            with open(local_path, "wb") as handle:
                handle.write(content)
        return repo_path

    async def get_files(self, agent_id: str, org_id: str, ref: str = "HEAD") -> Dict[str, str]:
        """Return every tracked file's path and text at a ref (a SHA, a branch or HEAD)."""
        self._check_git()
        repo_path = await self._download_repo(agent_id, org_id)
        try:

            def _get_files() -> Dict[str, str]:
                listing = _run_git(["ls-tree", "-r", "--name-only", ref], cwd=repo_path)
                names = listing.stdout.strip().split("\n") if listing.stdout.strip() else []
                files: Dict[str, str] = {}
                for name in names:
                    if name:
                        files[name] = _run_git(["show", f"{ref}:{name}"], cwd=repo_path).stdout
                return files

            return await asyncio.to_thread(_get_files)
        finally:
            shutil.rmtree(os.path.dirname(repo_path), ignore_errors=True)

    async def commit(
        self,
        agent_id: str,
        org_id: str,
        changes: List[FileChange],
        message: str,
        author_name: str = "Letta Agent",
        author_email: str = "agent@letta.ai",
        branch: str = "main",
    ) -> MemoryCommit:
        """Apply file changes as one commit and return its details; commits to one agent queue up."""
        lock = self._locks.setdefault(f"{org_id}/{agent_id}", asyncio.Lock())
        async with lock:
            return await self._commit_with_lock(
                agent_id, org_id, changes, message, author_name, author_email, branch
            )

    async def _commit_with_lock(
        self,
        agent_id: str,
        org_id: str,
        changes: List[FileChange],
        message: str,
        author_name: str,
        author_email: str,
        branch: str,
    ) -> MemoryCommit:
        """Copy the repository out, apply the changes, commit, and copy back what changed."""
        self._check_git()
        repo_path = await self._download_repo(agent_id, org_id)
        try:
            before_snapshot = self._snapshot_git_files(os.path.join(repo_path, ".git"))

            def _commit() -> MemoryCommit:
                _run_git(["config", "user.name", author_name], cwd=repo_path)
                _run_git(["config", "user.email", author_email], cwd=repo_path)
                _run_git(["reset", "--hard"], cwd=repo_path)
                parent = _run_git(["rev-parse", "HEAD"], cwd=repo_path, check=False)
                parent_sha = parent.stdout.strip() if parent.returncode == 0 else None
                files_changed: List[str] = []
                additions = deletions = 0
                for change in changes:
                    file_path = change.path.lstrip("/")
                    full_path = os.path.join(repo_path, file_path)
                    if change.change_type == "delete" or change.content is None:
                        if os.path.exists(full_path):
                            with open(full_path, "r") as handle:
                                deletions += len(handle.read())
                            os.remove(full_path)
                            _run_git(["rm", "-f", file_path], cwd=repo_path, check=False)
                    else:
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        if os.path.exists(full_path):
                            with open(full_path, "r") as handle:
                                deletions += len(handle.read())
                        additions += len(change.content)
                        with open(full_path, "w", encoding="utf-8") as handle:
                            handle.write(change.content)
                        _run_git(["add", file_path], cwd=repo_path)
                    files_changed.append(file_path)
                _run_git(["commit", "-m", message], cwd=repo_path)
                sha = _run_git(["rev-parse", "HEAD"], cwd=repo_path).stdout.strip()
                return MemoryCommit(
                    sha=sha,
                    parent_sha=parent_sha,
                    message=message,
                    author_type="agent" if "agent" in author_email.lower() else "user",
                    author_id=agent_id,
                    author_name=author_name,
                    timestamp=datetime.now(timezone.utc),
                    files_changed=files_changed,
                    additions=additions,
                    deletions=deletions,
                )

            commit = await asyncio.to_thread(_commit)
            await self._upload_delta(repo_path, agent_id, org_id, before_snapshot)
            return commit
        finally:
            shutil.rmtree(os.path.dirname(repo_path), ignore_errors=True)

    async def get_history(
        self, agent_id: str, org_id: str, path: Optional[str] = None, limit: int = 50
    ) -> List[MemoryCommit]:
        """Return up to limit commits, newest first, optionally only those touching a path."""
        self._check_git()
        repo_path = await self._download_repo(agent_id, org_id)
        try:

            def _history() -> List[MemoryCommit]:
                args = ["log", "--format=%H|%P|%an|%at|%s", f"-n{limit}"]
                if path:
                    args.extend(["--", path])
                out = _run_git(args, cwd=repo_path).stdout.strip()
                commits = []
                for line in out.split("\n") if out else []:
                    parts = line.split("|", 4)
                    if len(parts) < 5:
                        continue
                    sha, parents, author_name, stamp, message = parts
                    commits.append(
                        MemoryCommit(
                            sha=sha,
                            parent_sha=parents.split()[0] if parents else None,
                            message=message,
                            author_type="system",
                            author_id="",
                            author_name=author_name,
                            timestamp=datetime.fromtimestamp(int(stamp), tz=timezone.utc),
                        )
                    )
                return commits

            return await asyncio.to_thread(_history)
        finally:
            shutil.rmtree(os.path.dirname(repo_path), ignore_errors=True)

    async def get_head_sha(self, agent_id: str, org_id: str) -> str:
        """Return the SHA of an agent's current HEAD commit."""
        self._check_git()
        repo_path = await self._download_repo(agent_id, org_id)
        try:
            return await asyncio.to_thread(
                lambda: _run_git(["rev-parse", "HEAD"], cwd=repo_path).stdout.strip()
            )
        finally:
            shutil.rmtree(os.path.dirname(repo_path), ignore_errors=True)

    async def delete_repo(self, agent_id: str, org_id: str) -> None:
        """Delete an agent's whole repository from storage."""
        await self.storage.delete_prefix(self._repo_path(agent_id, org_id))


# Client --------------------------------------------------------------------------------------


class MemfsClient:
    """Read and write an agent's memory blocks as files in its git repository.

    It takes the base folder for storage. Every call takes the organization id and the acting
    user's id as plain strings. Every write is a commit, so history is complete. It never keeps
    block state in memory between calls.
    """

    def __init__(self, local_path: str):
        """Make the storage backend and git operations for the base folder."""
        self.local_path = local_path
        self.storage = LocalStorageBackend(base_path=local_path)
        self.git = GitOperations(storage=self.storage)

    async def close(self) -> None:
        """Close the client; local storage has nothing to close."""

    async def create_repo_async(
        self,
        agent_id: str,
        organization_id: str,
        actor_id: str,
        initial_blocks: Optional[List[PydanticBlock]] = None,
    ) -> str:
        """Create an agent's repository with the blocks as the first commit; return its HEAD SHA."""
        initial_files = {
            f"{block.label}.md": serialize_block(
                value=block.value or "",
                description=block.description,
                limit=block.limit,
                read_only=block.read_only,
                metadata=block.metadata,
            )
            for block in (initial_blocks or [])
        }
        return await self.git.create_repo(
            agent_id=agent_id,
            org_id=organization_id,
            initial_files=initial_files,
            author_name=f"User {actor_id}",
            author_email=f"{actor_id}@letta.ai",
        )

    async def get_blocks_async(
        self, agent_id: str, organization_id: str, ref: str = "HEAD"
    ) -> List[PydanticBlock]:
        """Return the agent's blocks at a ref; no repository means no blocks.

        A file skills/{name}/SKILL.md is the block skills/{name}; other markdown under skills/
        and every non-markdown file are not blocks. A block's id is made from the agent and label.
        """
        try:
            files = await self.git.get_files(agent_id, organization_id, ref)
        except FileNotFoundError:
            return []
        blocks = []
        for file_path, content in files.items():
            label = memory_block_label_from_markdown_path(file_path)
            if label is None:
                continue
            parsed = parse_block_markdown(content)
            synthetic = uuid.UUID(
                hashlib.md5(f"{agent_id}:{label}".encode(), usedforsecurity=False).hexdigest()
            )
            blocks.append(
                PydanticBlock(
                    id=f"block-{synthetic}",
                    label=label,
                    value=parsed["value"],
                    description=parsed.get("description"),
                    limit=parsed.get("limit", CORE_MEMORY_BLOCK_CHAR_LIMIT),
                    read_only=parsed.get("read_only", False),
                    metadata=parsed.get("metadata", {}),
                )
            )
        return blocks

    async def get_block_async(
        self, agent_id: str, label: str, organization_id: str, ref: str = "HEAD"
    ) -> Optional[PydanticBlock]:
        """Return one block of the agent at a ref, or None."""
        for block in await self.get_blocks_async(agent_id, organization_id, ref):
            if block.label == label:
                return block
        return None

    async def _ensure_repo_exists(self, agent_id: str, organization_id: str, actor_id: str) -> str:
        """Return the repository's HEAD SHA, creating an empty repository first when needed."""
        try:
            return await self.git.get_head_sha(agent_id, organization_id)
        except FileNotFoundError:
            return await self.git.create_repo(
                agent_id=agent_id,
                org_id=organization_id,
                initial_files={},
                author_name=f"User {actor_id}",
                author_email=f"{actor_id}@letta.ai",
            )

    async def update_block_async(
        self,
        agent_id: str,
        label: str,
        value: str,
        organization_id: str,
        actor_id: str,
        message: Optional[str] = None,
        *,
        description: Optional[str] = None,
        limit: Optional[int] = None,
        read_only: bool = False,
        metadata: Optional[dict] = None,
    ) -> MemoryCommit:
        """Commit a new value for a block (creating the repository if needed); return the commit."""
        await self._ensure_repo_exists(agent_id, organization_id, actor_id)
        content = serialize_block(
            value=value,
            description=description,
            limit=limit,
            read_only=read_only,
            metadata=metadata,
        )
        return await self.git.commit(
            agent_id=agent_id,
            org_id=organization_id,
            changes=[FileChange(path=f"{label}.md", content=content, change_type="modify")],
            message=message or f"Update {label}",
            author_name=f"User {actor_id}",
            author_email=f"{actor_id}@letta.ai",
        )

    async def create_block_async(
        self,
        agent_id: str,
        block: PydanticBlock,
        organization_id: str,
        actor_id: str,
        message: Optional[str] = None,
    ) -> MemoryCommit:
        """Commit a new block file (creating the repository if needed); return the commit."""
        await self._ensure_repo_exists(agent_id, organization_id, actor_id)
        content = serialize_block(
            value=block.value or "",
            description=block.description,
            limit=block.limit,
            read_only=block.read_only,
            metadata=block.metadata,
        )
        return await self.git.commit(
            agent_id=agent_id,
            org_id=organization_id,
            changes=[FileChange(path=f"{block.label}.md", content=content, change_type="add")],
            message=message or f"Create block {block.label}",
            author_name=f"User {actor_id}",
            author_email=f"{actor_id}@letta.ai",
        )

    async def delete_block_async(
        self,
        agent_id: str,
        label: str,
        organization_id: str,
        actor_id: str,
        message: Optional[str] = None,
    ) -> MemoryCommit:
        """Commit the deletion of a block file; its earlier versions stay in the history."""
        await self._ensure_repo_exists(agent_id, organization_id, actor_id)
        return await self.git.commit(
            agent_id=agent_id,
            org_id=organization_id,
            changes=[FileChange(path=f"{label}.md", content=None, change_type="delete")],
            message=message or f"Delete block {label}",
            author_name=f"User {actor_id}",
            author_email=f"{actor_id}@letta.ai",
        )

    async def get_history_async(
        self,
        agent_id: str,
        organization_id: str,
        path: Optional[str] = None,
        limit: int = 50,
    ) -> List[MemoryCommit]:
        """Return the agent's commits, newest first; no repository means no history."""
        try:
            return await self.git.get_history(
                agent_id=agent_id, org_id=organization_id, path=path, limit=limit
            )
        except FileNotFoundError:
            return []
