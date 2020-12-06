# coding=utf-8

"""Transmission Client."""

from __future__ import unicode_literals

import json
import logging
import os
import re
from base64 import b64encode

from medusa import app
from medusa.clients.torrent.generic import GenericClient
from medusa.logger.adapters.style import BraceAdapter

import requests.exceptions
from requests.compat import urljoin


log = BraceAdapter(logging.getLogger(__name__))
log.logger.addHandler(logging.NullHandler())


class TransmissionAPI(GenericClient):
    """Transmission API class."""

    def __init__(self, host=None, username=None, password=None):
        """Transmission constructor.

        :param host:
        :type host: string
        :param username:
        :type username: string
        :param password:
        :type password: string
        """
        super(TransmissionAPI, self).__init__('Transmission', host, username, password)

        self.rpcurl = self.rpcurl.strip('/')
        self.url = urljoin(self.host, self.rpcurl + '/rpc')

    def check_response(self):
        """Check if response is a valid json and its a success one."""
        try:
            return self.response.json()['result'] == 'success'
        except ValueError:
            return False

    def _get_auth(self):

        post_data = json.dumps({
            'method': 'session-get',
            'user': self.username,
            'password': self.password
        })

        try:
            self.response = self.session.post(self.url, data=post_data.encode('utf-8'), timeout=120,
                                              verify=app.TORRENT_VERIFY_CERT)
        except requests.exceptions.ConnectionError as error:
            log.warning('{name}: Unable to connect. {error}',
                        {'name': self.name, 'error': error})
            return False
        except requests.exceptions.Timeout as error:
            log.warning('{name}: Connection timed out. {error}',
                        {'name': self.name, 'error': error})
            return False

        auth_match = re.search(r'X-Transmission-Session-Id:\s*(\w+)', self.response.text)

        if not auth_match:
            return False

        if auth_match:
            self.auth = auth_match.group(1)

        self.session.headers.update({'x-transmission-session-id': self.auth})

        # Validating Transmission authorization
        post_data = json.dumps({
            'arguments': {},
            'method': 'session-get',
        })

        self._request(method='post', data=post_data)

        # remove me later
        result = self._torrent_properties('6ed4c48cf23f453a90cef2022e3e8a72f5b786ae')

        return self.auth

    def _add_torrent_uri(self, result):

        arguments = {
            'filename': result.url,
            'paused': 1 if app.TORRENT_PAUSED else 0
        }
        if os.path.isabs(app.TORRENT_PATH):
            arguments['download-dir'] = app.TORRENT_PATH

        post_data = json.dumps({
            'arguments': arguments,
            'method': 'torrent-add',
        })

        self._request(method='post', data=post_data)

        return self.check_response()

    def _add_torrent_file(self, result):

        arguments = {
            'metainfo': b64encode(result.content).decode('utf-8'),
            'paused': 1 if app.TORRENT_PAUSED else 0
        }

        if os.path.isabs(app.TORRENT_PATH):
            arguments['download-dir'] = app.TORRENT_PATH

        post_data = json.dumps({
            'arguments': arguments,
            'method': 'torrent-add',
        })

        self._request(method='post', data=post_data)

        return self.check_response()

    def _set_torrent_ratio(self, result):

        ratio = None
        if result.ratio:
            ratio = result.ratio

        mode = 0
        if ratio:
            if float(ratio) == -1:
                ratio = 0
                mode = 2
            elif float(ratio) >= 0:
                ratio = float(ratio)
                mode = 1  # Stop seeding at seedRatioLimit

        arguments = {
            'ids': [result.hash],
            'seedRatioLimit': ratio,
            'seedRatioMode': mode,
        }

        post_data = json.dumps({
            'arguments': arguments,
            'method': 'torrent-set',
        })

        self._request(method='post', data=post_data)

        return self.check_response()

    def _set_torrent_seed_time(self, result):

        if app.TORRENT_SEED_TIME and app.TORRENT_SEED_TIME != -1:
            time = int(60 * float(app.TORRENT_SEED_TIME))
            arguments = {
                'ids': [result.hash],
                'seedIdleLimit': time,
                'seedIdleMode': 1,
            }

            post_data = json.dumps({
                'arguments': arguments,
                'method': 'torrent-set',
            })

            self._request(method='post', data=post_data)

            return self.check_response()
        else:
            return True

    def _set_torrent_priority(self, result):

        arguments = {'ids': [result.hash]}

        if result.priority == -1:
            arguments['priority-low'] = []
        elif result.priority == 1:
            # set high priority for all files in torrent
            arguments['priority-high'] = []
            # move torrent to the top if the queue
            arguments['queuePosition'] = 0
            if app.TORRENT_HIGH_BANDWIDTH:
                arguments['bandwidthPriority'] = 1
        else:
            arguments['priority-normal'] = []

        post_data = json.dumps({
            'arguments': arguments,
            'method': 'torrent-set',
        })

        self._request(method='post', data=post_data)

        return self.check_response()

    def remove_torrent(self, info_hash):
        """Remove torrent from client using given info_hash.

        :param info_hash:
        :type info_hash: string
        :return
        :rtype: bool
        """
        arguments = {
            'ids': [info_hash],
            'delete-local-data': 1,
        }

        post_data = json.dumps({
            'arguments': arguments,
            'method': 'torrent-remove',
        })

        self._request(method='post', data=post_data)

        return self.check_response()

    def move_torrent(self, info_hash):
        """Set new torrent location given info_hash.

        :param info_hash:
        :type info_hash: string
        :return
        :rtype: bool
        """
        if not app.TORRENT_SEED_LOCATION or not info_hash:
            return

        arguments = {
            'ids': [info_hash],
            'location': app.TORRENT_SEED_LOCATION,
            'move': 'true'
        }

        post_data = json.dumps({
            'arguments': arguments,
            'method': 'torrent-set-location',
        })

        self._request(method='post', data=post_data)

        return self.check_response()

    def _torrent_properties(self, info_hash):
        """Get torrent properties."""
        log.info('Checking {client} torrent {hash} status.', {'client': self.name, 'hash': info_hash})

        return_params = {
            'ids': info_hash,
            'fields': ['name', 'hashString', 'percentDone', 'status',
                       'isStalled', 'errorString', 'seedRatioLimit',
                       'isFinished', 'uploadRatio', 'seedIdleLimit', 'activityDate']
        }

        post_data = json.dumps({'arguments': return_params, 'method': 'torrent-get'})

        if not self._request(method='post', data=post_data) or not self.check_response():
            log.warning('Error while fetching torrent {hash} status.', {'hash': info_hash})
            return

        torrent = self.response.json()['arguments']['torrents']
        if not torrent:
            log.warning('Error while fetching torrent {hash} status.', {'hash': info_hash})
            return

        return torrent[0]

    def torrent_completed(self, info_hash):
        """Check if torrent has finished downloading."""
        properties = self._torrent_properties(info_hash)
        return properties['status'] == 6 or self.torrent_seeded(info_hash)

    def torrent_seeded(self, info_hash):
        """Check if torrent has finished seeding."""
        properties = self._torrent_properties(info_hash)
        return properties['status'] == 0 and properties['isFinished']


api = TransmissionAPI
