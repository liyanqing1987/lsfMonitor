import os
import re
import sys
import time
import datetime

sys.path.append(str(os.environ['LSFMONITOR_INSTALL_PATH']) + '/monitor')
from common import common
from conf import config

os.environ['PYTHONUNBUFFERED'] = '1'


def get_license_info(specified_feature=''):
    """
    Get EDA liecnse feature usage and expires information.
    Save it into a dict.
    """
    license_dic = {}
    license_server = ''
    license_files = ''
    lmgrd_status = ''
    feature = ''
    expires_mark = False

    # Get lmstat command.
    if config.lmstat_path:
        lmstat = str(config.lmstat_path) + '/lmstat'

        if os.path.exists(lmstat):
            command = str(lmstat) + ' -a -i'
    else:
        command = 'lmstat -a -i'

    if specified_feature:
        command = str(command) + ' ' + str(specified_feature)

    if 'lmstat_bsub_command' in os.environ:
        command = str(os.environ['lmstat_bsub_command']) + ' "' + str(command) + '"'
    elif config.lmstat_bsub_command:
        command = str(config.lmstat_bsub_command) + ' "' + str(command) + '"'

    (return_code, stdout, stderr) = common.run_command(command)

    # Parse lmstat output message.
    for line in str(stdout, 'utf-8').split('\n'):
        line = line.strip()

        if re.match('^License server status: (\S+)\s*$', line):
            my_match = re.match('^License server status: (\S+)\s*$', line)
            license_server = my_match.group(1)
            license_dic.setdefault(license_server, {})
            expires_mark = False
        elif re.match('^\s*License file\(s\) on (\S+): (\S+):\s*$', line):
            my_match = re.match('^\s*License file\(s\) on (\S+): (\S+):\s*$', line)
            license_host = re.sub('.*@', '', license_server)

            if my_match.group(1) != license_host:
                print('*Error*: Not find "License file(s) ..." information for license server "' + str(license_server) + '".')
                sys.exit(1)

            license_files = my_match.group(2)
            license_file_list = license_files.split(':')
            license_dic[license_server].setdefault('license_files', license_file_list)
        elif re.search('license server UP', line):
            lmgrd_status = 'up'
            license_dic[license_server].setdefault('status', lmgrd_status)
        elif re.search('license server DOWN', line):
            lmgrd_status = 'down'
            license_dic[license_server].setdefault('status', lmgrd_status)
        elif re.match('^Users of (\S+):  \(Total of ([0-9]+) license(s?) issued;  Total of ([0-9]+) license(s?) in use\)\s*$', line):
            my_match = re.match('^Users of (\S+):  \(Total of ([0-9]+) license(s?) issued;  Total of ([0-9]+) license(s?) in use\)\s*$', line)
            feature = my_match.group(1)
            issued_num = my_match.group(2)
            in_use_num = my_match.group(4)

            license_dic[license_server].setdefault('feature', {})
            license_dic[license_server]['feature'].setdefault(feature, {})
            license_dic[license_server]['feature'][feature].setdefault('issued', issued_num)
            license_dic[license_server]['feature'][feature].setdefault('in_use', in_use_num)
            license_dic[license_server]['feature'][feature].setdefault('in_use_info', [])
        elif re.search(', start ', line):
            license_dic[license_server]['feature'][feature]['in_use_info'].append(line)
        elif re.match('^Feature .* Expires\s*$', line):
            expires_mark = True
            license_dic[license_server].setdefault('expires', {})
        elif expires_mark and re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(permanent\(no expiration date\)|[0-9]{1,2}-[a-zA-Z]{3}-[0-9]{4})\s*$', line):
            my_match = re.match('^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(permanent\(no expiration date\)|[0-9]{1,2}-[a-zA-Z]{3}-[0-9]{4})\s*$', line)
            feature = my_match.group(1)
            version = my_match.group(2)
            license = my_match.group(3)
            vendor = my_match.group(4)
            expires = my_match.group(5)
            license_dic[license_server]['expires'].setdefault(feature, [])
            license_dic[license_server]['expires'][feature].append({'version': version, 'license': license, 'vendor': vendor, 'expires': expires})

    return(license_dic)


def filter_license_feature(license_dic, features=[], servers=[], mode='ALL'):
    """
    Only keep specified features on license_dic.
    """
    new_license_dic = {}

    for (license_server, license_server_dic) in license_dic.items():
        if (not servers) or ('ALL' in servers) or (license_server in servers):
            license_files = ' '.join(license_server_dic['license_files'])
            license_status = license_server_dic['status']

            # Get filtered feature information. (filtered by feature and in_use mode)
            new_feature_list = []
            new_feature_dic = {}
            max_feature_length = 0

            if 'feature' in license_server_dic.keys():
                for (feature, feature_dic) in license_server_dic['feature'].items():
                    if feature in features:
                        issued_num = feature_dic['issued']
                        in_use_num = feature_dic['in_use']
                        in_use_info = feature_dic['in_use_info']

                        if (mode == 'ALL') or ((mode == 'in_use') and (in_use_num != '0')):
                            new_feature_list.append(feature)
                            new_feature_dic.setdefault(feature, {'issued': issued_num, 'in_use': in_use_num, 'in_use_info': in_use_info})

                            if len(feature) > max_feature_length:
                                max_feature_length = len(feature)

            # Save new_license_dic.
            if new_feature_dic:
                new_license_dic.setdefault(license_server, {})
                new_license_dic[license_server].setdefault('license_files', license_files)
                new_license_dic[license_server].setdefault('status', license_status)

                if new_feature_dic:
                    new_license_dic[license_server].setdefault('feature', new_feature_dic)
                    new_license_dic[license_server].setdefault('max_feature_length', max_feature_length)

                # Save expires information.
                if 'expires' in license_server_dic.keys():
                    for (feature, feature_dic_list) in license_server_dic['expires'].items():
                        if feature in new_feature_list:
                            for feature_dic in feature_dic_list:
                                version = feature_dic['version']
                                license = feature_dic['license']
                                vendor = feature_dic['vendor']
                                expires = feature_dic['expires']

                                new_license_dic[license_server].setdefault('expires', {})
                                new_license_dic[license_server]['expires'].setdefault(feature, [])
                                new_license_dic[license_server]['expires'][feature].append({'version': version, 'license': license, 'vendor': vendor, 'expires': expires})

    return(new_license_dic)


def check_long_runtime(start_time, second_threshold=259200):
    """
    Runtime is more than second_threshold (default is 3 days), return True.
    Runtime is less than second_threshold (default is 3 days), return False.
    """
    if start_time:
        current_year = datetime.date.today().year
        start_time = str(current_year) + ' ' + str(start_time)
        start_seconds = int(time.mktime(time.strptime(start_time, '%Y %a %m/%d %H:%M')))
        current_seconds = int(time.time())

        if start_seconds > current_seconds:
            current_year = int(datetime.date.today().year) - 1
            start_seconds = int(time.mktime(time.strptime(start_time, '%Y %a %m/%d %H:%M')))

        if current_seconds - start_seconds >= second_threshold:
            return(True)

    return(False)


def check_expire_date(expire_date, second_threshold=1209600):
    """
    Expired, return -1.
    Expire in second_threshold (default is 14 days), return day number.
    Expire later than second_threshold (default is 14 days), return 0.
    """
    if re.search('permanent', expire_date):
        return(0)
    else:
        expire_seconds = int(time.mktime(time.strptime(expire_date, '%d-%b-%Y')))
        expire_seconds = expire_seconds + 86400
        current_seconds = int(time.time())

        if expire_seconds < current_seconds:
            return(-1)
        elif expire_seconds - current_seconds <= second_threshold:
            return((expire_seconds - current_seconds)//86400 + 1)
        else:
            return(0)
