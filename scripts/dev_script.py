import os
import shutil
import tempfile

from qfieldsync.core.offline_converter import OfflineConverter
from qfieldsync.tests.utilities import test_data_folder
from qgis.core import Qgis, QgsProject, QgsRectangle, QgsOfflineEditing
from qgis.testing import start_app, unittest
from qgis.testing.mocked import get_iface

from qgis.core import QgsApplication
QgsApplication.setPrefixPath(r'C:\OSGEO4w', useDefaultPaths=True)
os.environ['PROJ_LIB'] = r'C:\Program Files\QGIS 3.18\share\proj'
#os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = r'C:\OSGeo4W64\apps\Qt5\plugins'

#start_app()


export_location = r'F:\tmp\export'
import_location = r'F:\tmp\import'

qgis_project_file = r'F:\tmp\qgis_projects\Okarito Data Mgmt 316.qgz'
simple_project = r'C:\Users\Nicholas\Documents\GitHub\qfieldsync\qfieldsync\tests\data\simple_project\project.qgs'

qgs_project = QgsProject.instance()
qgs_project.clear()
#qgs_project.read(qgis_project_file)