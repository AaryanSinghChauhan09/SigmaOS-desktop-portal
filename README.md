    # SigmaOS-desktop-portal

    XDG desktop portal backend (screenshot, files)

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/linuxmint/xdg-desktop-portal-xapp

    ## Design principles applied
    - **Separation of Concerns**
- **Plan Before Act**
- **Defensive Programming**
- **Fail-Fast**
- **KISS**
- **DRY**
- **Deterministic Testing**

    ## Usage
    Verbs: `portals, screenshot-plan, files, remote`

    ```
    $ sigma-portal portals --config-dir /etc/xdg-desktop-portal
backends: 2   portals: 12   missing: none
$ sigma-portal screenshot-plan --interactive --delay 2
Screenshot(parent, {"interactive": true, "delay": 2})
$ sigma-portal files --mode open --multiple --mime image/png image/jpeg
OpenFile(parent, {"multiple": true, ...})
$ sigma-portal remote --actions pointer keyboard --persist

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
