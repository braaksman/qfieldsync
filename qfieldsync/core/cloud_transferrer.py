# -*- coding: utf-8 -*-
"""
/***************************************************************************
 QFieldSync
                             -------------------
        begin                : 2020-08-19
        git sha              : $Format:%H$
        copyright            : (C) 2020 by OPENGIS.ch
        email                : info@opengis.ch
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""


from typing import List
import shutil
from pathlib import Path

from qgis.PyQt.QtCore import pyqtSignal, QObject
from qgis.PyQt.QtNetwork import QNetworkReply
from qgis.core import QgsProject

from qfieldsync.core.cloud_api import CloudNetworkAccessManager
from qfieldsync.core.cloud_project import CloudProject, ProjectFile, ProjectFileCheckout


class CloudTransferrer(QObject):
    # TODO show progress of individual files
    progress = pyqtSignal(float)
    error = pyqtSignal(str, Exception)
    abort = pyqtSignal()
    finished = pyqtSignal()
    upload_progress = pyqtSignal(float)
    download_progress = pyqtSignal(float)
    upload_finished = pyqtSignal()
    download_finished = pyqtSignal()
    delete_finished = pyqtSignal()


    def __init__(self, network_manager: CloudNetworkAccessManager, cloud_project: CloudProject) -> None:
        super(CloudTransferrer, self).__init__(parent=None)

        self.network_manager = network_manager
        self.cloud_project = cloud_project
        self._files_to_upload = {}
        self._files_to_download = {}
        self._files_to_delete = {}
        self.upload_files_finished = 0
        self.upload_bytes_total_files_only = 0
        self.download_files_finished = 0
        self.download_bytes_total_files_only = 0
        self.delete_files_finished = 0
        self.is_aborted = False
        self.is_started = False
        self.is_finished = False
        self.is_upload_active = False
        self.is_download_active = False
        self.is_delete_active = False
        self.is_project_list_update_active = False
        self.replies = []
        self.temp_dir = Path(QgsProject.instance().homePath()).joinpath('.qfieldsync')

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

        self.temp_dir.mkdir()
        self.temp_dir.joinpath('backup').mkdir()
        self.temp_dir.joinpath('upload').mkdir()
        self.temp_dir.joinpath('download').mkdir()

        self.upload_finished.connect(self._on_upload_finished)
        self.download_finished.connect(self._on_download_finished)

        self.network_manager.logout_success.connect(self._on_logout_success)


    def sync(self, files_to_upload: List[ProjectFile], files_to_download: List[ProjectFile], files_to_delete: List[ProjectFile]) -> None:
        assert not self.is_started
        
        self.is_started = True

        # prepare the files to be uploaded, copy them in a temporary destination
        for project_file in files_to_upload:
            assert project_file.local_path

            filename = project_file.name

            temp_filename = self.temp_dir.joinpath('upload', filename)
            temp_filename.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project_file.local_path, temp_filename)

            self.upload_bytes_total_files_only += project_file.local_size
            self._files_to_upload[filename] = {
                'project_file': project_file,
                'bytes_total': project_file.local_size,
                'bytes_transferred': 0,
                'temp_filename': temp_filename,
            }
        
        # prepare the files to be delete, both locally and remotely
        for project_file in files_to_delete:
            filename = project_file.name

            self._files_to_delete[filename] = {
                'project_file': project_file,
            }

        # prepare the files to be downloaded, download them in a temporary destination
        for project_file in files_to_download:
            filename = project_file.name

            temp_filename = self.temp_dir.joinpath('download', filename)
            temp_filename.parent.mkdir(parents=True, exist_ok=True)

            self.download_bytes_total_files_only += project_file.size
            self._files_to_download[filename] = {
                'project_file': project_file,
                'bytes_total': project_file.size,
                'bytes_transferred': 0,
                'temp_filename': temp_filename,
            }

        self._make_backup()
        self._upload()


    def _upload(self) -> None:
        assert not self.is_upload_active, 'Upload in progress'
        assert not self.is_download_active, 'Download in progress'

        self.is_upload_active = True

        # nothing to upload
        if len(self._files_to_upload) == 0:
            self.upload_progress.emit(1)
            self.upload_finished.emit()
            # NOTE check _on_upload_finished
            return

        assert self.cloud_project.local_dir

        for filename in self._files_to_upload:
            temp_filename = self._files_to_upload[filename]['temp_filename']

            reply = self.network_manager.cloud_upload_files('files/' + self.cloud_project.id + '/' + filename, filenames=[str(temp_filename)])
            reply.uploadProgress.connect(lambda s, t: self._on_upload_file_progress(reply, s, t, filename=filename))
            reply.finished.connect(lambda: self._on_upload_file_finished(reply, filename=filename))

            self.replies.append(reply)


    def _delete(self) -> None:
        self.is_delete_active = True

        # nothing to delete
        if len(self._files_to_delete) == 0:
            self.delete_finished.emit()
            # NOTE check _on_delete_finished
            return

        for filename in self._files_to_delete:
            project_file = self._files_to_delete[filename]['project_file']

            if project_file.checkout == ProjectFileCheckout.Local:
                self.delete_files_finished += 1
                Path(project_file.local_path).unlink()

            if project_file.checkout & ProjectFileCheckout.Cloud:
                if project_file.checkout & ProjectFileCheckout.Local:
                    Path(project_file.local_path).unlink()

                reply = self.network_manager.delete_file(self.cloud_project.id + '/' + str(filename) + '/')
                reply.finished.connect(lambda: self._on_delete_file_finished(reply, filename=filename))

        # in case all the files to delete were local only
        if self.delete_files_finished == len(self._files_to_delete):
            self.delete_finished.emit()


    def _download(self) -> None:
        assert not self.is_upload_active, 'Upload in progress'
        assert not self.is_download_active, 'Download in progress'

        self.is_download_active = True

        # nothing to download
        if len(self._files_to_download) == 0:
            self.download_progress.emit(1)
            self.download_finished.emit()
            return

        for filename in self._files_to_download:
            temp_filename = self._files_to_download[filename]['temp_filename']

            reply = self.network_manager.get_file(self.cloud_project.id + '/' + str(filename) + '/', str(temp_filename))
            reply.downloadProgress.connect(lambda r, t: self._on_download_file_progress(reply, r, t, filename=filename))
            reply.finished.connect(lambda: self._on_download_file_finished(reply, filename=filename, temp_filename=temp_filename))

            self.replies.append(reply)


    def _on_upload_file_progress(self, reply: QNetworkReply, bytes_sent: int, bytes_total: int, filename: str) -> None:
        # there are always at least a few bytes to send, so ignore this situation
        if bytes_total == 0:
            return

        self._files_to_upload[filename]['bytes_transferred'] = bytes_sent
        self._files_to_upload[filename]['bytes_total'] = bytes_total

        bytes_sent_sum = sum([self._files_to_upload[filename]['bytes_transferred'] for filename in self._files_to_upload])
        bytes_total_sum = max(sum([self._files_to_upload[filename]['bytes_total'] for filename in self._files_to_upload]), self.upload_bytes_total_files_only)

        fraction = min(bytes_sent_sum / bytes_total_sum, 1) if self.upload_bytes_total_files_only > 0 else 1

        self.upload_progress.emit(fraction)


    def _on_upload_file_finished(self, reply: QNetworkReply, filename: str) -> None:
        try:
            self.network_manager.handle_response(reply, False)
        except Exception as err:
            self.error.emit(self.tr('Uploading file "{}" failed.'.format(filename)), err)
            self.abort_requests()
            return

        self.upload_files_finished += 1

        if self.upload_files_finished == len(self._files_to_upload):
            self.upload_progress.emit(1)
            self.upload_finished.emit()
            # NOTE check _on_upload_finished
            return


    def _on_download_file_progress(self, reply: QNetworkReply, bytes_received: int, bytes_total: int, filename: str = '') -> None:
        self._files_to_download[filename]['bytes_transferred'] = bytes_received
        self._files_to_download[filename]['bytes_total'] = bytes_total

        bytes_received_sum = sum([self._files_to_download[filename]['bytes_transferred'] for filename in self._files_to_download])
        bytes_total_sum = max(sum([self._files_to_download[filename]['bytes_total'] for filename in self._files_to_download]), self.download_bytes_total_files_only)

        fraction = min(bytes_received_sum / bytes_total_sum, 1) if self.download_bytes_total_files_only > 0 else 1

        self.download_progress.emit(fraction)


    def _on_delete_file_finished(self, reply: QNetworkReply, filename: str) -> None:
        self.delete_files_finished += 1

        try:
            self.network_manager.handle_response(reply, False)
        except Exception as err:
            self.error.emit(self.tr(f'Deleting file "{filename}" failed.'), err)

        if self.delete_files_finished == len(self._files_to_delete):
            self.delete_finished.emit()


    def _on_download_file_finished(self, reply: QNetworkReply, filename: str = '', temp_filename: str = '') -> None:
        try:
            self.network_manager.handle_response(reply, False)
        except Exception as err:
            self.error.emit(self.tr('Downloading file "{}" failed. Aborting...'.format(filename)), err)
            self.abort_requests()
            return

        self.download_files_finished += 1

        if not Path(temp_filename).exists():
            self.error.emit(self.tr('Downloaded file "{}" not found!'.format(temp_filename)))
            self.abort_requests()
            return

        if self.download_files_finished == len(self._files_to_download):
            self._temp_dir2main_dir('download')
            self.download_progress.emit(1)
            self.download_finished.emit()
            return


    def _on_upload_finished(self) -> None:
        self.is_upload_active = False
        self._update_project_files_list()
        self._delete()


    def _on_delete_finished(self) -> None:
        self.is_delete_active = False
        self._download()


    def _on_download_finished(self) -> None:
        self.is_download_active = False
        self.is_finished = True
        self.import_qfield_project()

        if not self.is_project_list_update_active:
            self.finished.emit()


    def _update_project_files_list(self) -> None:
        self.is_project_list_update_active = True

        reply = self.network_manager.projects_cache.get_project_files(self.cloud_project.id)
        reply.finished.connect(lambda: self._on_update_project_files_list_finished())


    def _on_update_project_files_list_finished(self) -> None:
        self.is_project_list_update_active = False

        if not self.is_download_active:
            self.finished.emit()


    def abort_requests(self) -> None:
        if self.is_aborted:
            return

        self.is_aborted = True

        for reply in self.replies:
            if not reply.isFinished():
                reply.abort()

        self.abort.emit()


    def _make_backup(self) -> None:
        for project_file in [
            *list(map(lambda f: f['project_file'], self._files_to_upload.values())),
            *list(map(lambda f: f['project_file'], self._files_to_download.values())),
        ]:
            if project_file.local_path and project_file.local_path.exists():
                dest = self.temp_dir.joinpath('backup', project_file.path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                
                shutil.copyfile(project_file.local_path, dest)


    def _temp_dir2main_dir(self, subdir: str) -> None:
        subdir_path = self.temp_dir.joinpath(subdir)

        assert subdir_path.exists()

        for filename in subdir_path.glob('**/*'):
            if filename.is_dir():
                filename.mkdir(parents=True, exist_ok=True)
                continue

            relative_filename = str(Path(filename).relative_to(subdir_path))

            shutil.copyfile(filename, self._files_to_download[relative_filename]['project_file'].local_path)


    def import_qfield_project(self) -> None:
        try:
            self._temp_dir2main_dir(str(self.temp_dir.joinpath('download')))
        except Exception as err:
            self.error.emit('Failed to copy downloaded files to your project. Trying to rollback changes...', err)
            try:
                self._temp_dir2main_dir(str(self.temp_dir.joinpath('backup')))
            except Exception as errInner:
                self.error.emit('Failed to rollback the backup. You project might be corrupted! Please check ".qfieldsync/backup" directory and try to copy the files back manually.', errInner)


    def _on_logout_success(self) -> None:
        self.projectsTable.setRowCount(0)
        self.abort_requests()

        self.close()
