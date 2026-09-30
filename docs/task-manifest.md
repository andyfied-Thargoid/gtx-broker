# Owned repository and task-manifest boundary

Before the scheduler admits automated coding work, the task must carry a
versioned manifest and pass the owned-repository preflight.

The manifest records:

- task, goal, and session IDs;
- the owned repository name and allowed remote;
- base ref, non-default branch, and isolated worktree path;
- repository-relative context documents, bounded scope, and acceptance
  criteria;
- deterministic test command, worker, timeout, and context budget; and
- mandatory commit, pull-request, Air Review, Codex final review, and merge
  policy.

`RepositoryRegistry.compute01_defaults()` is the explicit allow-list for the
owned compute01 repositories. A path is not sufficient to establish
ownership: preflight also verifies that the resolved Git worktree is inside an
allowed root, is clean, is on the manifest branch, and has an allowed remote.
Linked worktrees may live under `/home/andyfied/src/worktrees`, but their Git
remote still has to identify the named repository.

The validator is intentionally separate from the legacy queue ingress while
existing image and compatibility tasks are migrated. New automated coding
admission must call `validate_task_manifest()` with live worktree checking
enabled; `check_worktree=False` is only for schema/unit tests.

The boundary does not grant permission to modify host configuration or another
repository. Workstation deployment, systemd, model profiles, and sandbox
configuration remain workstation-owned.

After a manifest coding handler succeeds, the broker records the task as
`awaiting_review`, not `succeeded`. The generic vision-review approval endpoint
cannot promote a manifest task. Production activation remains deliberately
deferred until the required Air Review, Codex final review, and pull-request
approval workflow is integrated.
