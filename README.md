# Kalandra

Kalandra can be used to mirror multiple Git repositories across different locations
in an efficient manner without having to keep a full copy of any of the repositories.

## How it works

All changes in Git are kept as objects (commits, tags, trees, etc.). Branches and tags are
just references to objects. The [Git Protocol][] allows for discovering what objects
are present on the given server. It also allows to [negotiate][] the minimal set of objects
(or deltas of objects) that need to be transferred to synchronize with the server.

The naive approach to mirroring a Git repository is:

```shell
# First time
git clone --mirror upstream

# Every X minutes
git fetch --prune upstream
git push --mirror downstream
```

This works fine most of the time, but requires that you maintain
a local copy the repository that you are mirroring.

What Kalandra does instead, is to that it acts as a broker between
the two servers. No data is persisted by the running service. We negotiate
the minimal pack needed to syncronize the mirror with upstream and then
only transfer the pack.

[Git Protocol]: https://git-scm.com/book/en/v2/Git-on-the-Server-The-Protocols
[negotiate]: https://git-scm.com/docs/gitprotocol-v2#_fetch

## Roadmap

Completed features:

- [X] support for ``file://`` targets.
- [X] basic support for ``ssh://`` targets using [AsyncSSH](https://github.com/ronf/asyncssh).
- [X] support for ``http://`` targets.
- [X] GitHub App token authentication for HTTP

Planned features (in order of priority):

- [ ] add "sync-to-github" command which updates a mirror on GitHub based on metadata configured
  in [GH custom properties](https://docs.github.com/en/organizations/managing-organization-settings/managing-custom-properties-for-repositories-in-your-organization#about-custom-properties)
- [ ] package as OCI image.
- [ ] optimize use of capabilities like ``ofs-delta``.

## Contributing

TBD

## License

Copyright (c) 2024-2025 Łukasz Rekucki

[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0)
