# -*- coding: utf-8 -*-
################################
# File Name   : app_context.py
# Author      : liyanqing.1987
# Created On : 2026-08-24
# Description : AppContext holds shared state across panels so they can access
#               cluster info, db paths and each other without depending on
#               MainWindow directly.
################################


class AppContext:
    """Shared context passed to every Panel.

    Panels may read: install_path, cluster/tool/cluster_db_path (None before
    LSF panel init), sibling panel refs (set after construction), dark_mode."""

    def __init__(self, install_path, dark_mode=False):
        self.install_path = install_path
        self.dark_mode = dark_mode

        # Populated by LsfPanel during init.
        self.tool = None
        self.cluster = None
        self.cluster_db_path = None
        self.license_dic = {}

        # Sibling panel refs, wired up by MainWindow after all panels built.
        self.lsf_panel = None
        self.license_panel = None
        self.run_panel = None
        self.ai_panel = None

    def get_lsf_host_info(self):
        """Return per-host LSF info {host: {status, queue, group}}, same source
        as the HOSTS tab. Refreshes bhosts/host_queue/host_group caches first.
        Returns {} if LSF data is unavailable."""
        if self.lsf_panel is None:
            return {}

        refresh = getattr(self.lsf_panel, 'fresh_lsf_info', None)

        for key in ('bhosts', 'host_queue', 'host_group'):
            if refresh is not None:
                refresh(key)

        bhosts_dic = getattr(self.lsf_panel, 'bhosts_dic', {})
        host_queue_dic = getattr(self.lsf_panel, 'host_queue_dic', {})
        host_group_dic = getattr(self.lsf_panel, 'host_group_dic', {})

        host_list = bhosts_dic.get('HOST_NAME', [])
        status_list = bhosts_dic.get('STATUS', [])

        info = {}

        for (i, host) in enumerate(host_list):
            status = status_list[i] if i < len(status_list) else ''

            queue = ''

            if host in host_queue_dic:
                queue = ' '.join(host_queue_dic[host])

            group = ''

            if host in host_group_dic:
                group = ' '.join(host_group_dic[host])

            info[host] = {'status': status, 'queue': queue, 'group': group}

        return info
