"""Module docstring."""

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)


class BareGitRemote:
    """Class docstring."""

    def __init__(self, agent_id: str, remote_path: str = None):
        """Method docstring."""
        self.agent_id = agent_id
        if remote_path:
            self.remote_path = remote_path
        else:
            self.remote_path = os.path.join(
                tempfile.gettempdir(), f"letta_memfs_remote_{self.agent_id}.git"
            )

        if not os.path.exists(self.remote_path):
            self._init_bare_repo()

    def _init_bare_repo(self):
        """Method docstring."""
        os.makedirs(self.remote_path, exist_ok=True)
        subprocess.run(
            ["git", "init", "--bare"], cwd=self.remote_path, check=True, capture_output=True
        )
        logger.info(f"Initialized bare git remote at {self.remote_path}")

    def get_remote_url(self) -> str:
        """Method docstring."""
        return self.remote_path


class GitWorktree:
    """Class docstring."""

    def __init__(self, remote_url: str):
        """Method docstring."""
        self.remote_url = remote_url
        self.work_dir = tempfile.mkdtemp(prefix="letta_memfs_work_")
        self._clone()

    def _clone(self):
        """Method docstring."""
        try:
            subprocess.run(
                ["git", "clone", self.remote_url, "."],
                cwd=self.work_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            # If bare repo is empty, we just init and add remote
            subprocess.run(["git", "init"], cwd=self.work_dir, check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", self.remote_url],
                cwd=self.work_dir,
                check=True,
                capture_output=True,
            )

    def write_file(self, filepath: str, content: str):
        """Method docstring."""
        full_path = os.path.join(self.work_dir, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)

    def read_file(self, filepath: str) -> str:
        """Method docstring."""
        full_path = os.path.join(self.work_dir, filepath)
        if not os.path.exists(full_path):
            return None
        with open(full_path, "r") as f:
            return f.read()

    def commit_and_push(self, message: str = "Update memory"):
        """Method docstring."""
        subprocess.run(["git", "add", "."], cwd=self.work_dir, check=True, capture_output=True)

        # Check if there are changes
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.work_dir, capture_output=True, text=True
        )
        if not status.stdout.strip():
            return

        subprocess.run(["git", "config", "user.name", "Letta Agent"], cwd=self.work_dir, check=True)
        subprocess.run(
            ["git", "config", "user.email", "agent@letta.local"], cwd=self.work_dir, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", message], cwd=self.work_dir, check=True, capture_output=True
        )

        # Push to origin main
        try:
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=self.work_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                ["git", "push", "-u", "origin", "master"],
                cwd=self.work_dir,
                check=False,
                capture_output=True,
            )

    def pull(self):
        """Method docstring."""
        try:
            subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=self.work_dir,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                ["git", "pull", "origin", "master"],
                cwd=self.work_dir,
                check=False,
                capture_output=True,
            )

    def cleanup(self):
        """Method docstring."""
        import shutil

        shutil.rmtree(self.work_dir, ignore_errors=True)
