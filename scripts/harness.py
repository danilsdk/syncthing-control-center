#!/usr/bin/env python3
"""Repository-local task harness. Python 3.10+, Git; no third-party packages."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest


class HarnessError(Exception):
    """An actionable configuration or workflow error."""


def run(args: list[str], root: Path, *, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            args, cwd=root, text=True, encoding="utf-8", errors="strict",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None, check=False,
        )
    except FileNotFoundError as exc:
        raise HarnessError(f"Команда недоступна: {args[0]}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise HarnessError(f"Не выполнено: {' '.join(args)}\n{detail}")
    return result.stdout or ""


def git(root: Path, *args: str) -> str:
    return run(["git", *args], root)


def repository() -> Path:
    return Path(run(["git", "rev-parse", "--show-toplevel"], Path.cwd()).strip())


def load_json(root: Path, path: str, ref: str | None = None) -> dict:
    try:
        raw = git(root, "show", f"{ref}:{path}") if ref else (root / path).read_text()
        value = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise HarnessError(f"Невозможно прочитать {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"{path}: нужен JSON-объект")
    return value


def load_task(root: Path, task_id: str, ref: str | None = None) -> dict:
    task_id = task_id.upper()
    if not re.fullmatch(r"[ABH][0-9]+", task_id):
        raise HarnessError(f"Некорректный ID задачи: {task_id}")
    return load_json(root, f".harness/tasks/{task_id}.json", ref)


def tasks(root: Path, ref: str | None = None) -> dict[str, dict]:
    if ref:
        names = git(root, "ls-tree", "-r", "--name-only", ref, ".harness/tasks").splitlines()
    else:
        names = [str(p.relative_to(root)) for p in sorted((root / ".harness/tasks").glob("*.json"))]
    return {Path(name).stem: load_json(root, name, ref) for name in names if name.endswith(".json")}


def path_matches(path: str, pattern: str) -> bool:
    """A trailing /** matches a subtree; * and ? never cross a slash."""
    if pattern == "**":
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    expression = re.escape(pattern).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.fullmatch(expression, path) is not None


def allowed(task: dict, path: str) -> bool:
    return any(path_matches(path, p) for p in task["allow"]) and not any(
        path_matches(path, p) for p in task.get("deny", [])
    )


def owner(project: dict, path: str) -> str | None:
    result = None
    for item in project["ownership"]:
        if path_matches(path, item["path"]):
            result = item["role"]
    return result


def codeowners_text(project: dict) -> str:
    lines = ["# Generated from .harness/project.json; run: python3 scripts/harness.py codeowners", "# Last matching rule wins."]
    for item in project["ownership"]:
        pattern = item["path"]
        github_pattern = "*" if pattern == "**" else "/" + pattern
        if github_pattern.endswith("/**"):
            github_pattern = github_pattern[:-2]
        login = project["roles"][item["role"]]["github"]
        lines.append(f"{github_pattern} @{login}")
    return "\n".join(lines) + "\n"


def resolve_task(root: Path, task_id: str | None, branch: str | None = None,
                 policy_ref: str | None = None) -> dict:
    branch = branch or git(root, "branch", "--show-current").strip()
    task_map = tasks(root, policy_ref)
    if task_id:
        task = load_task(root, task_id, policy_ref)
        if branch != task["branch"]:
            raise HarnessError(f"Задача {task['id']} требует ветку {task['branch']}; сейчас {branch or 'detached HEAD'}")
        return task
    for task in task_map.values():
        if task["branch"] == branch:
            return task
    raise HarnessError(f"Ветка {branch or 'detached HEAD'} не соответствует карточке задачи")


def diff_paths(root: Path, *args: str) -> set[str]:
    """Include both old and new names: renaming a foreign file is an edit."""
    fields = git(root, "diff", "--name-status", "-z", "--find-renames", *args).split("\0")
    result: set[str] = set()
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        i += 1
        count = 2 if status.startswith(("R", "C")) else 1
        if i + count > len(fields):
            raise HarnessError("Некорректный ответ git diff")
        result.update(fields[i:i + count])
        i += count
    return result


def fingerprint(root: Path, path: str) -> str:
    file = root / path
    if file.is_symlink():
        return "symlink:" + os.readlink(file)
    if not file.is_file():
        return "missing"
    digest = hashlib.sha256()
    with file.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: Path, task: dict) -> None:
    path = root / ".harness/local" / f"{task['id']}.json"
    if path.exists():
        raise HarnessError("Снимок начала задачи уже существует; он не перезаписывается")
    foreign = {}
    for name in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if name and not allowed(task, name):
            foreign[name] = fingerprint(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"task": task["id"], "head": git(root, "rev-parse", "HEAD").strip(),
                                "preexisting_untracked": foreign}, ensure_ascii=False, indent=2) + "\n")
    print(f"Снимок начала {task['id']}: {len(foreign)} существующих чужих untracked-файлов")


def changed_paths(root: Path, task: dict, base: str, *, staged: bool = False,
                  committed_only: bool = False) -> set[str]:
    if staged:
        return diff_paths(root, "--cached")
    result = diff_paths(root, f"{base}...HEAD")
    if committed_only:
        return result
    result.update(diff_paths(root))
    result.update(diff_paths(root, "--cached"))
    local = root / ".harness/local" / f"{task['id']}.json"
    known = load_json(root, str(local.relative_to(root))).get("preexisting_untracked", {}) if local.exists() else {}
    for path in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path and not (path in known and fingerprint(root, path) == known[path]):
            result.add(path)
    return result


def check_scope(root: Path, task: dict, base: str, *, staged: bool = False,
                committed_only: bool = False) -> set[str]:
    paths = changed_paths(root, task, base, staged=staged, committed_only=committed_only)
    bad = sorted(path for path in paths if not allowed(task, path))
    if bad:
        raise HarnessError(f"Выход за границы {task['id']}:\n" + "\n".join(f"  {p}" for p in bad))
    print(f"Границы {task['id']}: проверено {len(paths)} файлов")
    return paths


def files(root: Path) -> list[Path]:
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    return [root / name for name in sorted(set(names)) if name and (root / name).is_file()]


def check_structure(root: Path) -> None:
    project = load_json(root, ".harness/project.json")
    task_map = tasks(root)
    if not task_map:
        raise HarnessError("Нет карточек задач")
    errors = []
    branches = set()
    for task_id, task in task_map.items():
        required = {"id", "role", "title", "branch", "depends_on", "allow", "spec", "acceptance"}
        if not required <= task.keys():
            errors.append(f"{task_id}: отсутствуют поля {required - task.keys()}")
            continue
        if task["id"] != task_id or task["role"] not in project["roles"]:
            errors.append(f"{task_id}: некорректный ID/роль")
        if not re.fullmatch(r"codex/[abh][0-9]+-[a-z0-9-]+", task["branch"]):
            errors.append(f"{task_id}: некорректная ветка")
        if task["branch"] in branches:
            errors.append(f"{task_id}: повтор ветки")
        branches.add(task["branch"])
        if not task["allow"] or not task["acceptance"]:
            errors.append(f"{task_id}: пустые пути/критерии")
        for pattern in task["allow"] + task.get("deny", []):
            if pattern.startswith("/") or ".." in pattern.split("/") or "\\" in pattern:
                errors.append(f"{task_id}: опасный путь {pattern}")
        spec = root / task["spec"].split("#", 1)[0]
        if not spec.is_file():
            errors.append(f"{task_id}: отсутствует описание {spec.name}")
        for dep in task["depends_on"]:
            if dep not in task_map:
                errors.append(f"{task_id}: неизвестная зависимость {dep}")
    visiting, visited = set(), set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            errors.append(f"Циклическая зависимость: {task_id}")
            return
        if task_id in visited or task_id not in task_map:
            return
        visiting.add(task_id)
        for dep in task_map[task_id].get("depends_on", []):
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_map:
        visit(task_id)
    for path in sorted((root / ".harness/checks").glob("*.json")):
        check = load_json(root, str(path.relative_to(root)))
        commands = check.get("commands", [])
        if not check.get("when_exists") or not commands:
            errors.append(f"{path.name}: проверка не должна быть пустой")
        for command in commands + check.get("setup_commands", []):
            if not isinstance(command, list) or not command or not all(isinstance(word, str) and word for word in command):
                errors.append(f"{path.name}: команды задаются непустыми списками аргументов")
        cwd = Path(check.get("cwd", "."))
        if cwd.is_absolute() or ".." in cwd.parts:
            errors.append(f"{path.name}: рабочая папка должна оставаться внутри проекта")
    if (root / ".github/CODEOWNERS").read_text() != codeowners_text(project):
        errors.append("CODEOWNERS отличается от карты владения; запусти команду codeowners")
    for path in files(root):
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(), filename=str(path))
            except (SyntaxError, UnicodeError) as exc:
                errors.append(f"{path.relative_to(root)}: {exc}")
        if path.suffix != ".md":
            continue
        data = path.read_text()
        # Ignore fenced examples; templates can intentionally reference future artifacts.
        prose = re.sub(r"^```[^\n]*\n.*?^```\s*$", "", data, flags=re.M | re.S)
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", prose):
            if re.match(r"[a-z]+:", target) or target.startswith("#"):
                continue
            target = target.split("#", 1)[0].strip("<>")
            if not (path.parent / target).exists():
                errors.append(f"{path.relative_to(root)}: ссылка не существует {target}")
    if errors:
        raise HarnessError("\n".join(errors))
    git(root, "diff", "--check")
    git(root, "diff", "--cached", "--check")
    print(f"Структура harness: {len(task_map)} карточек, роли, зависимости, ссылки и Python — OK")


def self_test(root: Path) -> None:
    suite = unittest.defaultTestLoader.discover(str(root / "scripts/tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.testsRun == 0:
        raise HarnessError("Проверки самого harness не прошли")


def python_runtime(root: Path) -> str:
    for candidate in (root / ".venv/bin/python", root / ".venv/Scripts/python.exe"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def product_checks(root: Path) -> None:
    active = 0
    for path in sorted((root / ".harness/checks").glob("*.json")):
        check = load_json(root, str(path.relative_to(root)))
        if not any(list(root.glob(pattern)) for pattern in check["when_exists"]):
            print(f"НЕ ПРИМЕНИМО: {check['name']} — компонент ещё не создан")
            continue
        active += 1
        print(f"Проверка: {check['name']}", flush=True)
        cwd = (root / check.get("cwd", ".")).resolve()
        if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
            raise HarnessError(f"Неверная рабочая папка проверки: {cwd}")
        for command in check["commands"]:
            args = [python_runtime(root) if word == "{python}" else word for word in command]
            run(args, cwd, capture=False)
    if active == 0:
        print("Проверен harness. Проверки приложения пока неприменимы; MVP не объявляется готовым.")


def setup(root: Path) -> None:
    """Install declared development dependencies in the worktree, never globally."""
    for path in sorted((root / ".harness/checks").glob("*.json")):
        check = load_json(root, str(path.relative_to(root)))
        if not any(list(root.glob(pattern)) for pattern in check["when_exists"]):
            continue
        if any("{python}" in command for command in check.get("setup_commands", [])):
            if not (root / ".venv").exists():
                run([sys.executable, "-m", "venv", ".venv"], root, capture=False)
        cwd = (root / check.get("cwd", ".")).resolve()
        if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
            raise HarnessError(f"Неверная рабочая папка установки: {cwd}")
        for command in check.get("setup_commands", []):
            args = [python_runtime(root) if word == "{python}" else word for word in command]
            run(args, cwd, capture=False)
    print("Подготовлены существующие компоненты; отсутствующие компоненты не создавались")


def prompt(root: Path, task: dict) -> str:
    project = load_json(root, ".harness/project.json")
    person = project["roles"][task["role"]]
    lines = [f"Выполни задачу {task['id']}: {task['title']} в Syncthing Control Center.",
             f"Участник: {task['role']} / @{person['github']} ({person['focus']}).",
             f"Ветка: {task['branch']}. Работай в отдельном worktree этой задачи.",
             "Используй $scc-task. Прочитай AGENTS.md, нужные вложенные AGENTS.md,",
             f"docs/harness.md, {task['spec']} и существующий контракт.",
             "Ты не один в проекте. Сохраняй чужие изменения. Разрешённые пути:",
             *[f"- {p}" for p in task["allow"]],
             "Зависимости: " + (", ".join(task["depends_on"]) or "нет"),
             "Критерии готовности:", *[f"- {item}" for item in task["acceptance"]],
             f"Запусти python3 scripts/harness.py validate --task {task['id']}.",
             f"Запиши результаты в docs/agent-log/{task['role'].lower()}/{task['id']}.md.",
             "Проверки, пропущенные из-за среды, укажи явно. Выполни локальный commit",
             "только файлов задачи после успешных проверок. Для push/PR используй",
             "прямое поручение участника; готовый PR сам не объединяй."]
    return "\n".join(lines)


def start(root: Path, task: dict, args: argparse.Namespace) -> None:
    project = load_json(root, ".harness/project.json")
    base = args.base or f"{project['remote']}/{project['base_branch']}"
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", base) or base.startswith("-"):
        raise HarnessError("Некорректная исходная ветка")
    if not args.no_fetch:
        git(root, "fetch", project["remote"])
    # Uncommitted harness files are not copied into a worktree by Git.
    git(root, "cat-file", "-e", f"{base}:scripts/harness.py")
    git(root, "cat-file", "-e", f"{base}:.harness/tasks/{task['id']}.json")
    task = load_task(root, task["id"], base)
    destination = Path(args.path).expanduser().resolve() if args.path else root.parent / f"{root.name}-{task['id'].lower()}"
    if destination.exists():
        raise HarnessError(f"Папка уже существует: {destination}; используй её после проверки или выбери другую")
    git(root, "worktree", "add", "-b", task["branch"], str(destination), base)
    snapshot(destination, task)
    print(f"Рабочая папка: {destination}\nВетка: {task['branch']}")
    print(prompt(destination, task))


def install_hooks(root: Path) -> None:
    current = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=root,
                             text=True, capture_output=True, check=False).stdout.strip()
    if current and current != ".githooks":
        raise HarnessError(f"Уже настроены hooks: {current}. Существующую настройку не заменяем.")
    for path in (root / ".githooks").iterdir():
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o111)
    git(root, "config", "--local", "core.hooksPath", ".githooks")
    print("Локальные Git hooks включены для этого clone; глобальная конфигурация не изменена")


def pre_push(root: Path) -> None:
    head = git(root, "rev-parse", "HEAD").strip()
    for line in sys.stdin:
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        if not local_ref.startswith("refs/heads/codex/") or remote_ref != local_ref:
            raise HarnessError("Push разрешён только в одноимённую рабочую ветку codex/ задачи")
        if local_sha != head:
            raise HarnessError("Проверяй и отправляй ветку из её собственного worktree; удаление ветки этим hook не разрешено")
        task = resolve_task(root, None, local_ref.removeprefix("refs/heads/"))
        if set(remote_sha) != {"0"}:
            git(root, "merge-base", "--is-ancestor", remote_sha, local_sha)
        project = load_json(root, ".harness/project.json")
        base = f"{project['remote']}/{project['base_branch']}"
        check_scope(root, task, base)
        check_structure(root)
        self_test(root)
        product_checks(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "list", "check", "self-test", "codeowners", "install-hooks", "pre-push", "setup", "product-checks"):
        sub.add_parser(name)
    for name in ("show", "prompt", "snapshot", "start"):
        command = sub.add_parser(name)
        command.add_argument("task")
        if name == "start":
            command.add_argument("--path")
            command.add_argument("--base")
            command.add_argument("--no-fetch", action="store_true")
    for name in ("scope", "validate"):
        command = sub.add_parser(name)
        command.add_argument("--task")
        command.add_argument("--base", default="origin/main")
        command.add_argument("--branch")
        command.add_argument("--policy-ref")
        command.add_argument("--staged", action="store_true")
        command.add_argument("--committed-only", action="store_true")
    command = sub.add_parser("reviewer")
    command.add_argument("--branch", required=True)
    args = parser.parse_args()
    try:
        root = repository()
        if args.command == "doctor":
            print(f"Проект: {root}\nPython: {sys.version.split()[0]}\n{git(root, '--version').strip()}")
            print(f"Ветка: {git(root, 'branch', '--show-current').strip()}")
            print("Продуктовые зависимости устанавливаются отдельно в A0/B3.")
            check_structure(root)
        elif args.command == "list":
            for task in tasks(root).values():
                print(f"{task['id']:3} {task['role']} {task['branch']:30} {task['title']}")
        elif args.command in ("show", "prompt", "snapshot", "start"):
            task = load_task(root, args.task)
            if args.command == "show":
                print(json.dumps(task, ensure_ascii=False, indent=2))
            elif args.command == "prompt":
                print(prompt(root, task))
            elif args.command == "snapshot":
                snapshot(root, resolve_task(root, args.task))
            else:
                start(root, task, args)
        elif args.command == "check":
            check_structure(root)
        elif args.command == "self-test":
            self_test(root)
        elif args.command == "codeowners":
            destination = root / ".github/CODEOWNERS"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(codeowners_text(load_json(root, ".harness/project.json")))
            print("CODEOWNERS обновлён")
        elif args.command == "install-hooks":
            install_hooks(root)
        elif args.command == "pre-push":
            pre_push(root)
        elif args.command == "setup":
            setup(root)
        elif args.command == "product-checks":
            product_checks(root)
        elif args.command == "reviewer":
            task = resolve_task(root, None, args.branch)
            project = load_json(root, ".harness/project.json")
            peer = "A" if task["role"] == "B" else "B"
            print(project["roles"][peer]["github"])
        else:
            task = resolve_task(root, args.task, args.branch, args.policy_ref)
            check_scope(root, task, args.base, staged=args.staged, committed_only=args.committed_only)
            if args.command == "validate":
                check_structure(root)
                self_test(root)
                product_checks(root)
        return 0
    except (HarnessError, OSError, KeyError, UnicodeError) as exc:
        print(f"HARNESS: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
