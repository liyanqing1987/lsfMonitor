# -*- coding: utf-8 -*-
################################
# File Name   : patch.py
# Author      : liyanqing.1987
# Created On  : 2023-09-18 20:00:00
# Description : Patch/upgrade tool for lsfMonitor.
#               Syncs all files from a new package to the
#               existing installation, handles config migration,
#               shell wrapper regeneration, and cleanup.
################################
import os
import re
import sys
import shutil
import filecmp
import argparse
import datetime

sys.path.insert(0, str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common

os.environ['PYTHONUNBUFFERED'] = '1'


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument('-p', '--patch_path',
                        default='',
                        help='Specify patch path (new install package path).')
    parser.add_argument('-d', '--dry_run',
                        action='store_true',
                        default=False,
                        help='Preview changes without applying.')
    parser.add_argument('--no-backup',
                        dest='no_backup',
                        action='store_true',
                        default=False,
                        help='Skip backup creation before patching.')

    args = parser.parse_args()

    if not args.patch_path:
        common.bprint('Must specify patch path with "-p".', level='Error')
        sys.exit(1)

    if not os.path.exists(args.patch_path):
        common.bprint(f'"{args.patch_path}": No such patch path.', level='Error')
        sys.exit(1)

    return args.patch_path, args.dry_run, args.no_backup


class Patch():
    def __init__(self, patch_path, dry_run=False, no_backup=False):
        self.install_path = os.path.realpath(os.environ['LSFMONITOR_INSTALL_PATH'])
        self.patch_path = os.path.realpath(patch_path)
        self.dry_run = dry_run
        self.no_backup = no_backup

        # Directories excluded from sync entirely.
        self.exclude_dirs = {'db', '.git', '.claude', '__pycache__', '.patch_backup'}

        # Config file handled via migration, not direct copy.
        self.config_rel_path = 'monitor/conf/config.py'

        # Shell wrappers (generated, not synced directly).
        self.tool_list = [
            'monitor/bin/bmonitor',
            'monitor/bin/bsample',
            'monitor/tools/akill',
            'monitor/tools/check_issue_reason',
            'monitor/tools/patch',
            'monitor/tools/process_tracer',
            'monitor/tools/seedb',
            'monitor/tools/show_license_feature_usage'
        ]

        common.bprint(f'Install Path : {self.install_path}')
        common.bprint(f'Patch   Path : {self.patch_path}')
        common.bprint('')

        self.check_path_name()

    def check_path_name(self):
        """
        Make sure install_path and the patch_path have the same directory name.
        """
        if os.path.basename(self.install_path) != os.path.basename(self.patch_path):
            common.bprint(f'Current install path name is "{os.path.basename(self.install_path)}", but patch path name is "{os.path.basename(self.patch_path)}".', level='Warning')

            choice = input('Do you want to continue? (y|n) ')

            if choice.lower() in ('n', 'no'):
                sys.exit(0)
            else:
                common.bprint('')

    def is_excluded(self, rel_path):
        """
        Check if a relative path should be excluded from sync.
        """
        parts = rel_path.split('/')

        # Exclude top-level excluded directories.
        if parts[0] in self.exclude_dirs:
            return True

        # Exclude __pycache__ at any depth.
        if '__pycache__' in parts:
            return True

        # Exclude config file (handled via migration).
        if rel_path == self.config_rel_path:
            return True

        # Exclude generated shell wrappers.
        if rel_path in self.tool_list:
            return True

        return False

    def get_file_list(self, base_path):
        """
        Get all files from base_path as relative paths, excluding excluded dirs/files.
        """
        file_list = []

        for root_path, dirs, files in os.walk(base_path):
            # Compute relative dir path.
            rel_dir = os.path.relpath(root_path, base_path)

            if rel_dir == '.':
                rel_dir = ''

            # Prune excluded directories from traversal.
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs and '__pycache__' not in d]

            # Also prune if the full relative path is excluded.
            if rel_dir:
                parts = rel_dir.split(os.sep)
                if parts[0] in self.exclude_dirs or '__pycache__' in parts:
                    dirs[:] = []
                    continue

            for f in files:
                if rel_dir:
                    rel_file = rel_dir + '/' + f
                else:
                    rel_file = f

                if not self.is_excluded(rel_file):
                    file_list.append(rel_file)

        return file_list

    def scan(self):
        """
        Scan both paths and categorize files as new/modified/deleted/unchanged.
        """
        install_files = set(self.get_file_list(self.install_path))
        patch_files = set(self.get_file_list(self.patch_path))

        new_files = sorted(patch_files - install_files)
        deleted_files = sorted(install_files - patch_files)
        common_files = patch_files & install_files

        modified_files = []

        for rel_file in sorted(common_files):
            abs_install = os.path.join(self.install_path, rel_file)
            abs_patch = os.path.join(self.patch_path, rel_file)

            if not filecmp.cmp(abs_install, abs_patch, shallow=False):
                modified_files.append(rel_file)

        return new_files, modified_files, deleted_files

    def display_summary(self, new_files, modified_files, deleted_files):
        """
        Display summary of changes.
        """
        common.bprint('=== Patch Summary ===', color='cyan', display_method=1)
        common.bprint(f'  New files     : {len(new_files)}', color='green')
        common.bprint(f'  Modified files: {len(modified_files)}', color='yellow')
        common.bprint(f'  Deleted files : {len(deleted_files)}', color='red')
        common.bprint('')

        if new_files:
            common.bprint('[New Files]', color='green', display_method=1)

            for f in new_files:
                common.bprint(f'  + {f}', color='green')

            common.bprint('')

        if modified_files:
            common.bprint('[Modified Files]', color='yellow', display_method=1)

            for f in modified_files:
                common.bprint(f'  ~ {f}', color='yellow')

            common.bprint('')

        if deleted_files:
            common.bprint('[Deleted Files]', color='red', display_method=1)

            for f in deleted_files:
                common.bprint(f'  - {f}', color='red')

            common.bprint('')

        # Config migration info.
        new_config = os.path.join(self.patch_path, self.config_rel_path)
        old_config = os.path.join(self.install_path, self.config_rel_path)

        if os.path.exists(new_config) and os.path.exists(old_config):
            if not filecmp.cmp(old_config, new_config, shallow=False):
                common.bprint('[Config Migration]', color='cyan', display_method=1)
                common.bprint('  Config will be migrated (user values preserved, new variables added).', color='cyan')
                common.bprint('')

    def create_backup(self, modified_files, deleted_files):
        """
        Backup files that will be modified or deleted.
        """
        if self.no_backup:
            return None

        files_to_backup = modified_files + deleted_files

        if not files_to_backup:
            return None

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = os.path.join(self.install_path, '.patch_backup', timestamp)

        common.bprint(f'>>> Creating backup in "{backup_dir}"')

        for rel_file in files_to_backup:
            src = os.path.join(self.install_path, rel_file)
            dst = os.path.join(backup_dir, rel_file)

            if os.path.exists(src):
                dst_dir = os.path.dirname(dst)

                if not os.path.exists(dst_dir):
                    os.makedirs(dst_dir, exist_ok=True)

                shutil.copy2(src, dst)

        # Also backup config file.
        config_src = os.path.join(self.install_path, self.config_rel_path)

        if os.path.exists(config_src):
            config_dst = os.path.join(backup_dir, self.config_rel_path)
            os.makedirs(os.path.dirname(config_dst), exist_ok=True)
            shutil.copy2(config_src, config_dst)

        common.bprint(f'    Backed up {len(files_to_backup)} file(s) + config.py')
        common.bprint('')

        return backup_dir

    def sync_files(self, new_files, modified_files, deleted_files):
        """
        Copy new/modified files from patch to install, remove deleted files.
        """
        # Copy new files.
        if new_files:
            common.bprint('>>> Copying new files')

            for rel_file in new_files:
                src = os.path.join(self.patch_path, rel_file)
                dst = os.path.join(self.install_path, rel_file)
                dst_dir = os.path.dirname(dst)

                if not os.path.exists(dst_dir):
                    os.makedirs(dst_dir, exist_ok=True)

                shutil.copy2(src, dst)
                common.bprint(f'  + {rel_file}')

            common.bprint('')

        # Copy modified files.
        if modified_files:
            common.bprint('>>> Updating modified files')

            for rel_file in modified_files:
                src = os.path.join(self.patch_path, rel_file)
                dst = os.path.join(self.install_path, rel_file)

                shutil.copy2(src, dst)
                common.bprint(f'  ~ {rel_file}')

            common.bprint('')

        # Remove deleted files.
        if deleted_files:
            common.bprint('>>> Removing deleted files')

            for rel_file in deleted_files:
                dst = os.path.join(self.install_path, rel_file)

                if os.path.exists(dst):
                    os.remove(dst)
                    common.bprint(f'  - {rel_file}')

            common.bprint('')

            # Remove empty directories left after deletion.
            self.remove_empty_dirs()

    def remove_empty_dirs(self):
        """
        Remove empty directories under install_path (excluding excluded dirs).
        """
        for root_path, dirs, files in os.walk(self.install_path, topdown=False):
            rel_dir = os.path.relpath(root_path, self.install_path)

            if rel_dir == '.':
                continue

            parts = rel_dir.split(os.sep)

            if parts[0] in self.exclude_dirs:
                continue

            if not os.listdir(root_path):
                os.rmdir(root_path)
                common.bprint(f'  Removed empty directory: {rel_dir}')

    def migrate_config(self):
        """
        Migrate config file: preserve user values, add new variables from template.
        """
        old_config_path = os.path.join(self.install_path, self.config_rel_path)
        new_config_path = os.path.join(self.patch_path, self.config_rel_path)

        if not os.path.exists(new_config_path):
            return

        if not os.path.exists(old_config_path):
            # No existing config, just copy the new one.
            dst_dir = os.path.dirname(old_config_path)

            if not os.path.exists(dst_dir):
                os.makedirs(dst_dir, exist_ok=True)

            shutil.copy2(new_config_path, old_config_path)
            common.bprint('>>> Config: copied new config (no existing config found).')
            return

        if filecmp.cmp(old_config_path, new_config_path, shallow=False):
            common.bprint('>>> Config: no changes needed.')
            return

        common.bprint('>>> Migrating config file')

        # Parse old config: extract variable assignments.
        old_vars = {}
        old_var_lines = {}

        with open(old_config_path, 'r') as f:
            for line in f:
                match = re.match(r'^(\w+)\s*=\s*(.*)$', line)

                if match:
                    var_name = match.group(1)
                    old_vars[var_name] = match.group(2)
                    old_var_lines[var_name] = line

        # Read new config template and build migrated content.
        new_vars_seen = set()
        migrated_lines = []

        with open(new_config_path, 'r') as f:
            for line in f:
                match = re.match(r'^(\w+)\s*=\s*(.*)$', line)

                if match:
                    var_name = match.group(1)
                    new_vars_seen.add(var_name)

                    if var_name in old_var_lines:
                        # Preserve user's value.
                        migrated_lines.append(old_var_lines[var_name])
                    else:
                        # New variable, use template default.
                        migrated_lines.append(line)
                        common.bprint(f'    Added new config variable: {var_name}', color='green')
                else:
                    migrated_lines.append(line)

        # Append user-custom variables not in new template.
        custom_vars = [name for name in old_vars if name not in new_vars_seen]

        if custom_vars:
            migrated_lines.append('\n# User custom settings (preserved from previous version).\n')

            for var_name in custom_vars:
                migrated_lines.append(old_var_lines[var_name])
                common.bprint(f'    Preserved custom variable: {var_name}', color='cyan')

        # Write migrated config.
        with open(old_config_path, 'w') as f:
            f.writelines(migrated_lines)

        os.chmod(old_config_path, 0o777)
        common.bprint('    Config migration completed.')
        common.bprint('')

    def regenerate_shell_wrappers(self):
        """
        Regenerate shell wrapper scripts for all tools.
        """
        common.bprint('>>> Regenerating shell wrappers')

        # Detect the tool_list from install.py in install path (already patched).
        tool_list = self.tool_list[:]

        # Also check if the new install.py defines additional tools.
        install_py_path = os.path.join(self.install_path, 'install.py')

        if os.path.exists(install_py_path):
            try:
                with open(install_py_path, 'r') as f:
                    content = f.read()

                # Extract tool_list from install.py.
                match = re.search(r'self\.tool_list\s*=\s*\[(.*?)\]', content, re.DOTALL)

                if match:
                    tools_str = match.group(1)
                    found_tools = re.findall(r"'([^']+)'", tools_str)

                    if found_tools:
                        tool_list = found_tools
            except Exception:
                pass

        python_path = os.path.dirname(os.path.abspath(sys.executable))
        ld_library_path_setting = 'export LD_LIBRARY_PATH=$LSFMONITOR_INSTALL_PATH/lib:'

        if 'LD_LIBRARY_PATH' in os.environ:
            ld_library_path_setting += os.environ['LD_LIBRARY_PATH']

        for tool_name in tool_list:
            tool_path = os.path.join(self.install_path, tool_name)
            tool_py = tool_path + '.py'

            # Only generate wrapper if the .py file exists.
            if not os.path.exists(tool_py):
                common.bprint(f'    Skipped {tool_name} (.py not found)', color='yellow')
                continue

            tool_dir = os.path.dirname(tool_path)

            if not os.path.exists(tool_dir):
                os.makedirs(tool_dir, exist_ok=True)

            script_content = f"""#!/bin/bash

# Set python3 path.
export PATH={python_path}:$PATH

# Set install path.
export LSFMONITOR_INSTALL_PATH={self.install_path}

# Set LD_LIBRARY_PATH.
{ld_library_path_setting}

# Set input method for Qt5 (auto-detect ibus/fcitx).
if [ -z "$QT_IM_MODULE" ]; then
    if pgrep -x ibus-daemon > /dev/null 2>&1; then
        export QT_IM_MODULE=ibus
    elif pgrep -x fcitx > /dev/null 2>&1 || pgrep -x fcitx5 > /dev/null 2>&1; then
        export QT_IM_MODULE=fcitx
    fi
fi

# Execute {tool_name}.py
python3 $LSFMONITOR_INSTALL_PATH/{tool_name}.py "$@"
"""

            with open(tool_path, 'w') as f:
                f.write(script_content)

            os.chmod(tool_path, 0o755)
            common.bprint(f'    Generated: {tool_name}')

        common.bprint('')

    def cleanup_pycache(self):
        """
        Remove all __pycache__ directories under install path.
        """
        removed_count = 0

        for root_path, dirs, files in os.walk(self.install_path, topdown=False):
            for d in dirs:
                if d == '__pycache__':
                    pycache_path = os.path.join(root_path, d)

                    try:
                        shutil.rmtree(pycache_path)
                        removed_count += 1
                    except Exception:
                        pass

        if removed_count > 0:
            common.bprint(f'>>> Cleaned up {removed_count} __pycache__ directory(s).')
            common.bprint('')

    def run(self):
        """
        Main execution flow.
        """
        # Step 1: Scan.
        common.bprint('>>> Scanning files ...')
        new_files, modified_files, deleted_files = self.scan()
        common.bprint('')

        # Step 2: Display summary.
        self.display_summary(new_files, modified_files, deleted_files)

        if not new_files and not modified_files and not deleted_files:
            # Check config separately.
            old_config = os.path.join(self.install_path, self.config_rel_path)
            new_config = os.path.join(self.patch_path, self.config_rel_path)

            if os.path.exists(old_config) and os.path.exists(new_config) and not filecmp.cmp(old_config, new_config, shallow=False):
                pass  # Config migration still needed.
            else:
                common.bprint('No changes detected. Installation is up to date.')
                return

        # Dry run: stop here.
        if self.dry_run:
            common.bprint('(Dry run mode - no changes applied.)', color='cyan', display_method=1)
            return

        # Confirm before applying.
        total_changes = len(new_files) + len(modified_files) + len(deleted_files)
        choice = input(f'Apply {total_changes} change(s)? (y|n) ')

        if choice.lower() in ('n', 'no'):
            common.bprint('Patch cancelled.')
            return

        common.bprint('')

        # Step 3: Backup.
        backup_dir = self.create_backup(modified_files, deleted_files)

        # Step 4: Sync files.
        self.sync_files(new_files, modified_files, deleted_files)

        # Step 5: Migrate config.
        self.migrate_config()

        # Step 6: Regenerate shell wrappers.
        self.regenerate_shell_wrappers()

        # Step 7: Cleanup __pycache__.
        self.cleanup_pycache()

        # Step 8: Result summary.
        common.bprint('=== Patch Complete ===', color='green', display_method=1)
        common.bprint(f'  New      : {len(new_files)} file(s)')
        common.bprint(f'  Modified : {len(modified_files)} file(s)')
        common.bprint(f'  Deleted  : {len(deleted_files)} file(s)')

        if backup_dir:
            common.bprint(f'  Backup   : {backup_dir}')

        common.bprint('')
        common.bprint('Done, please enjoy it.')


################
# Main Process #
################
def main():
    (patch_path, dry_run, no_backup) = read_args()
    my_patch = Patch(patch_path, dry_run, no_backup)
    my_patch.run()


if __name__ == '__main__':
    main()
