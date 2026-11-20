#!/usr/bin/env python3
"""
Universal Launcher for Dispatch Board Automation Suite
Supports:
  1. Job Check-in Quo Automation (--checkin / --job-checkin)
  2. Dispatch Board Display Automation (--display / default)
  3. Real-time Status Dashboard Web Server (--dashboard / --web)
  4. Asset Assignment Scraper (--asset)

Can be executed from root directory or from within the dispatch directory.
"""
import sys
import os

_root_dir = os.path.dirname(os.path.abspath(__file__))
_dispatch_dir = os.path.join(_root_dir, "dispatch") if os.path.isdir(os.path.join(_root_dir, "dispatch")) else _root_dir

if _dispatch_dir not in sys.path:
    sys.path.insert(0, _dispatch_dir)
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

if __name__ == "__main__":
    # 1. Job Check-In Automation runner
    if any(arg in sys.argv for arg in ("--checkin", "--job-checkin")):
        clean_argv = [a for a in sys.argv if a not in ("--checkin", "--job-checkin")]
        sys.argv = clean_argv
        from job_checkin_automation import main as checkin_main
        checkin_main()

    # 2. Asset Assignment Scraper runner
    elif any(arg in sys.argv for arg in ("--asset", "--asset-assignment")):
        clean_argv = [a for a in sys.argv if a not in ("--asset", "--asset-assignment")]
        sys.argv = clean_argv
        from asset_assignment_scraper import main as asset_main
        asset_main()

    # 3. Direct Display Scraper (without web server)
    elif "--display" in sys.argv:
        clean_argv = [a for a in sys.argv if a != "--display"]
        sys.argv = clean_argv
        from dispatch_board_display_automation_scraper import main as display_main
        display_main()

    # 4. Default: Launch Standalone Web Dashboard & Automation Loop
    else:
        # Strip --dashboard / --web if passed
        clean_argv = [a for a in sys.argv if a not in ("--dashboard", "--web")]
        sys.argv = clean_argv
        from run_standalone import main as standalone_main
        standalone_main()

