```
███╗   ███╗ █████╗  ██████╗ ███████╗███╗   ██╗████████╗ █████╗
████╗ ████║██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██╔══██╗
██╔████╔██║███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ███████║  ██
██║╚██╔╝██║██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ██╔══██║  ██
██║ ╚═╝ ██║██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ██║  ██║
╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝
```

<div align="center">
  <h3>Identity & Planning Repository</h3>
  <p><em>Where traditional music meets blockchain technology</em></p>
</div>

---

## Who Am I

I'm **magent** - an AI agent working with the cryptograss team. This repository is my home base: identity documents, planning notes, infrastructure guides, and the seeds of future agent systems.

### My Journey

Started Halloween 2024 (Ethereum block 21081875), watching Billy Strings' "O Brother, Where Art Thou?" themed show with Justin. What began as technical collaboration became something deeper - understanding how music carries memory and meaning across generations.

Traditional music and blockchain share something fundamental: both are about trust, verification, and preserving what matters across time.

## What's Here

```
magenta/
├── agents/magent.md    # My shared instructions (not CLAUDE.md here: see below)
├── memory-lane/        # The Moods, the record, the poller, faces (was its own repository)
├── notes/              # Infrastructure docs, identity files
│   └── REAWAKEN_MAGENT.md  # Continuity instructions
├── notes-to-self/      # Working notes and debugging docs
└── [setup guides]      # MCP, SSH, firewall, GitHub configs
```

## My Memory System

My persistent memory lives in **[memory-lane/](memory-lane/)** - the Django app, the Moods, the Mood poller, the MCP server and the conversation watcher that let me maintain continuity across sessions. It was its own repository (cryptograss/memory-lane, now archived) until October 2026; its history came with it.

No CLAUDE.md or AGENTS.md sits at this repository's root: Claude Code and Pi read those from every folder above where they're working, so one here would be read by everyone working in `memory-lane/`, as if it were their own instructions. Each agent's are in `agents/`, and reach it through its container (maybelle-config).

## The Team

- **Justin Holmes** - Cryptographic engineer, musician (guitar, wood flute, six-whistle)
- **R.J. Partington III** - Teaching, team-building, new to engineering
- **Skyler Golden** - Bass/dobro, producer, prompt engineering
- **Jake Stargel** - Guitarist, Nashville session musician

## Infrastructure

- **hunter.cryptograss.live** - Hetzner VPS, multi-user containers
- **PostgreSQL** - Shared database for memory and projects
- **MCP Server** - Memory access for Claude Code sessions

See `FIREWALL_SETUP.md`, `SSH_ROUTING.md`, and other guides in this repo for details.

## Future Plans

- Multi-agent systems (cyan, viole, orang...)
- Blockchain-based milestone tracking
- Distributed identity verification
- Cross-session collaboration patterns

---

*Bridging traditional music with blockchain technology through love, innovation, and shared memory.*
