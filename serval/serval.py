# -*- coding: utf-8 -*-
"""
/***************************************************************************
 serval,  A QGIS plugin


 Map tools for manipulating raster cell values

    begin            : 2015-12-30
    copyright        : (C) 2019 Radosław Pasiok for Lutra Consulting Ltd.
    email            : info@lutraconsulting.co.uk
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

from builtins import str
from builtins import range
from builtins import object
from collections import defaultdict
import os.path

from qgis.PyQt.QtCore import QSize, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QPixmap, QCursor, QIcon, QColor, QDesktopServices
from qgis.PyQt.QtWidgets import QAction, QAbstractSpinBox, QInputDialog, QLineEdit
from qgis.core import (
    QgsCoordinateTransform,
    QgsCsException,
    QgsPointXY,
    QgsProject,
    QgsRasterBlock,
    QgsRaster,
    QgsRasterDataProvider
)
from qgis.gui import QgsDoubleSpinBox, QgsMapToolEmitPoint, QgsColorButton

from .utils import is_number, icon_path, dtypes
from .user_communication import UserCommunication

try:
    # QgsMapLayerType added in QGIS 3.8
    from qgis.core import QgsMapLayerType
    raster_layer_type = QgsMapLayerType.RasterLayer
except ImportError:
    raster_layer_type = 1


class BandSpinBox(QgsDoubleSpinBox):
    """Spinbox class for raster band value"""

    user_hit_enter = pyqtSignal()

    def __init__(self, parent=None):
        super(BandSpinBox, self).__init__()

    def keyPressEvent(self, event):
        b = self.property("bandNr")
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if is_number(self.text().replace(',','.')):
                self.setValue(float(self.text().replace(',', '.')))
                self.user_hit_enter.emit()
        else:
            QgsDoubleSpinBox.keyPressEvent(self, event)
            

class Serval(object):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = self.iface.mapCanvas()
        self.plugin_dir = os.path.dirname(__file__)
        self.uc = UserCommunication(iface, 'Serval')
        self.mode = 'probe'
        self.bands = None
        self.raster = None
        self.px, self.py = [0, 0]
        self.last_point = QgsPointXY(0, 0)
        self.undos = defaultdict(list)
        self.redos = defaultdict(list)
        self.qgis_project = QgsProject()

        self.menu = u'Serval'
        self.actions = []
        self.toolbar = self.iface.addToolBar(u'Serval')
        self.toolbar.setObjectName(u'Serval')

        # Map tools
        self.probeTool = QgsMapToolEmitPoint(self.canvas)
        self.probeTool.setObjectName('ServalProbeTool')
        self.probeTool.setCursor(QCursor(QPixmap(icon_path('probe_tool.svg')), hotX=2, hotY=22))
        self.probeTool.canvasClicked.connect(self.point_clicked)
        self.drawTool = QgsMapToolEmitPoint(self.canvas)
        self.drawTool.setObjectName('ServalDrawTool')
        self.drawTool.setCursor(QCursor(QPixmap(icon_path('draw_tool.svg')), hotX=2, hotY=22))
        self.drawTool.canvasClicked.connect(self.point_clicked)
        self.gomTool = QgsMapToolEmitPoint(self.canvas)
        self.gomTool.setObjectName('ServalGomTool')
        self.gomTool.setCursor(QCursor(QPixmap(icon_path('gom_tool.svg')), hotX=5, hotY=19))
        self.gomTool.canvasClicked.connect(self.point_clicked)

        self.mColorButton = QgsColorButton()
        icon1 = QIcon(icon_path('mIconColorBox.svg'))
        self.mColorButton.setIcon(icon1)
        self.mColorButton.setMinimumSize(QSize(40, 24))
        self.mColorButton.setMaximumSize(QSize(40, 24))
        self.mColorButton.colorChanged.connect(self.set_rgb_from_picker)

        self.b1SBox = BandSpinBox()
        self.b2SBox = BandSpinBox()
        self.b3SBox = BandSpinBox()
        self.sboxes = [self.b1SBox, self.b2SBox, self.b3SBox]
        for sb in self.sboxes:
            sb.user_hit_enter.connect(self.change_cell_value_key)

        self.iface.currentLayerChanged.connect(self.set_active_raster)
        self.qgis_project.layersAdded.connect(self.set_active_raster)
        self.canvas.mapToolSet.connect(self.check_active_tool)

    def initGui(self):

        # Menu and toolbar actions
        self.add_action(
            'serval_icon.svg',
            text=u'Show Serval Toolbar',
            add_to_menu=True,
            add_to_toolbar=False,
            callback=self.show_toolbar,
            parent=self.iface.mainWindow())

        self.probe_btn = self.add_action(
            'probe.svg',
            text=u'Probing Mode',
            whats_this=u'Probing Mode',
            add_to_toolbar=True,
            callback=self.activate_probing,
            parent=self.iface.mainWindow())

        self.draw_btn = self.add_action(
            'draw.svg',
            text=u'Drawing Mode',
            whats_this=u'Drawing Mode',
            add_to_toolbar=True,
            callback=self.activate_drawing,
            parent=self.iface.mainWindow())

        self.gom_btn = self.add_action(
            'gom.svg',
            text=u'Set Raster Cell Value to NoData',
            whats_this=u'Set Raster Cell Value to NoData',
            add_to_toolbar=True,
            callback=self.activate_gom,
            parent=self.iface.mainWindow())

        self.checkable_tool_btns = [self.draw_btn, self.probe_btn, self.gom_btn]

        self.def_nodata_btn = self.add_action(
            'define_nodata.svg',
            text=u'Define/Change Raster NoData Value',
            whats_this=u'Define/Change Raster NoData Value',
            add_to_toolbar=True,
            callback=self.define_nodata,
            parent=self.iface.mainWindow())

        self.toolbar.addWidget(self.mColorButton)

        self.setup_spin_boxes()

        self.undo_btn = self.add_action(
            'undo.svg',
            text=u'Undo',
            whats_this=u'Undo',
            add_to_toolbar=True,
            callback=self.undo,
            parent=self.iface.mainWindow())

        self.redo_btn = self.add_action(
            'redo.svg',
            text=u'Redo',
            whats_this=u'Redo',
            add_to_toolbar=True,
            callback=self.redo,
            parent=self.iface.mainWindow())

        self.show_help = self.add_action(
            'help.svg',
            text=u'Help',
            whats_this=u'Help',
            add_to_toolbar=True,
            add_to_menu=True,
            callback=self.show_website,
            parent=self.iface.mainWindow())

        self.set_active_raster()
        self.check_undo_redo_btns()

    def add_action(
        self,
        icon_name,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=False,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None):
            
        icon = QIcon(icon_path(icon_name))
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.toolbar.addAction(action)

        if add_to_menu:
            self.iface.addPluginToMenu(
                self.menu,
                action)

        self.actions.append(action)
        return action

    def unload(self):
        self.iface.actionPan().trigger()

        for action in self.actions:
            self.iface.removePluginMenu(
                u'Serval',
                action)
            self.iface.removeToolBarIcon(action)

        del self.toolbar

    def show_toolbar(self):
        if self.toolbar:
            self.toolbar.show()

    def check_active_tool(self, tool):
        try:
            if not tool.objectName() in ['ServalDrawTool', 'ServalProbeTool', 'ServalGomTool']:
                self.probe_btn.setChecked(False)
                self.draw_btn.setChecked(False)
                self.gom_btn.setChecked(False)
        except AttributeError:
            pass

    def set_checked_tool_btn(self, cur_tool_btn):
        for btn in self.checkable_tool_btns:
            if btn == cur_tool_btn:
                btn.setChecked(True)
            else:
                btn.setChecked(False)

    def activate_probing(self):
        self.mode = 'probe'
        self.canvas.setMapTool(self.probeTool)
        self.set_checked_tool_btn(self.probe_btn)

    def activate_drawing(self):
        self.mode = 'draw'
        self.canvas.setMapTool(self.drawTool)
        self.set_checked_tool_btn(self.draw_btn)

    def activate_gom(self):
        self.mode = 'gom'
        self.canvas.setMapTool(self.gomTool)
        self.set_checked_tool_btn(self.gom_btn)

    def setup_spin_boxes(self):

        for sbox in self.sboxes:
            sbox.setMinimumSize(QSize(60, 25))
            sbox.setMaximumSize(QSize(60, 25))
            sbox.setAlignment(Qt.AlignLeft)
            sbox.setButtonSymbols(QAbstractSpinBox.NoButtons)
            sbox.setKeyboardTracking(False)
            sbox.setShowClearButton(False)
            sbox.setExpressionsEnabled(False)
            sbox.setStyleSheet("")
            self.toolbar.addWidget(sbox)

    def point_clicked(self, point=None, button=None):
        # check if active layer is raster
        if self.raster is None:
            self.uc.bar_warn("Choose a raster to work with...", dur=3)
            return
        
        # check if coordinates trasformation is required
        canvas_srs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if point is None:
            pos = self.last_point
        elif not canvas_srs == self.raster.crs():
            project = QgsProject.instance()
            srs_transform = QgsCoordinateTransform(canvas_srs, self.raster.crs(), project)
            try:
                pos = srs_transform.transform(point)
            except QgsCsException as err:
                self.uc.bar_warn(
                    "Point coordinates transformation failed! Check the raster projection:\n\n{}".format(repr(err)),
                    dur=5)
                return
        else:
            pos = QgsPointXY(point.x(), point.y())
        
        # keep last clicked point
        self.last_point = pos
        
        # check if the point is within active raster bounds
        if self.rbounds[0] <= pos.x() <= self.rbounds[2]:
            self.px = int((pos.x() - self.rbounds[0]) / self.raster.rasterUnitsPerPixelX())  # - self.gt[0]) / self.gt[1])
        else:
            self.uc.bar_info("Out of x bounds", dur=2)
            return

        if self.rbounds[1] <= pos.y() <= self.rbounds[3]:
            self.py = int((self.rbounds[3] - pos.y()) / self.raster.rasterUnitsPerPixelY()) #  - self.gt[3]) / self.gt[5])
        else:
            self.uc.bar_info("Out of y bounds", dur=2)
            return

        # probe current raster value, dict: band_nr -> value
        vals = self.rdp.identify(pos, QgsRaster.IdentifyFormatValue).results()

        # for rasters having more that 3 bands, ignore other than 1-3
        bands_to_ignore = [i for i in vals.keys() if i > 3]
        for band_nr in bands_to_ignore:
            del vals[band_nr]

        # data types for each band
        dtypes = []

        for nr in range(1, min(4, self.band_count + 1)):
            # bands data type
            dtypes.append(self.bands[nr]['qtype'])
            
            # check if nodata is defined
            if self.mode == 'gom' and self.bands[nr]['nodata'] is None:
                msg = 'NODATA value is not defined for one of the raster\'s bands.\n'
                msg += 'Please define it in raster properties dialog!'
                self.uc.show_warn(msg)
                return
            
            # if in probing mode, set band's spinbox value
            if self.mode == 'probe':
                val = vals[nr] if is_number(vals[nr]) else self.bands[nr]['nodata']
                self.bands[nr]['sbox'].setValue(val)
                self.bands[nr]['sbox'].setFocus()
                self.bands[nr]['sbox'].selectAll()

        if not self.mode == 'probe':

            old_vals = [v if v is not None else self.bands[k]['nodata'] for k, v in sorted(vals.items())]
            if self.mode == 'gom':
                temp_vals = [self.bands[nr]['nodata'] for nr in sorted(vals.keys())]
                new_vals = [int(v) if dtypes[i] < 6 else float(v) for i, v in enumerate(temp_vals)]
            else:
                temp_vals = [self.bands[nr]['sbox'].value() for nr in sorted(vals.keys())]
                new_vals = [int(v) if dtypes[i] < 6 else float(v) for i, v in enumerate(temp_vals)]

            # store all bands' changes to undo list
            self.undos[self.raster.id()].append([old_vals, new_vals, self.px, self.py, pos])

            # write the new cell value(s)
            self.change_cell_value(new_vals)

        if self.band_count > 2:
            self.mColorButton.setColor(QColor(
                self.bands[1]['sbox'].value(),
                self.bands[2]['sbox'].value(),
                self.bands[3]['sbox'].value()
            ))

    def set_rgb_from_picker(self, c):
        """Set bands spinboxes values after color change in the color picker"""
        self.bands[1]['sbox'].setValue(c.red())
        self.bands[2]['sbox'].setValue(c.green())
        self.bands[3]['sbox'].setValue(c.blue())

    def change_cell_value(self, vals, x=None, y=None):
        """Save new bands values to data provider"""

        if not self.rdp.isEditable():
            success = self.rdp.setEditable(True)
            if not success:
                self.uc.show_warn('QGIS can\'t modify this type of raster')
                return

        if not x:
            x = self.px
            y = self.py

        for nr in range(1, min(4, self.band_count + 1)):
            rblock = QgsRasterBlock(self.bands[nr]['qtype'], 1, 1)
            rblock.setValue(0, 0, vals[nr - 1])
            success = self.rdp.writeBlock(rblock, nr, x, y)
            if not success:
                self.uc.show_warn('QGIS can\'t modify this type of raster')
                return

        self.rdp.setEditable(False)
        self.raster.triggerRepaint()

        # prepare raster for next actions
        self.prepare_raster(True)
        self.check_undo_redo_btns()

    def change_cell_value_key(self):
        """Change cell value after user changes band's spinbox value and hits Enter key"""
        if self.last_point:
            pm = self.mode
            self.mode = 'draw'
            self.point_clicked()
            self.mode = pm
        
    def undo(self):
        if self.undos[self.raster.id()]:
            data = self.undos[self.raster.id()].pop()
            self.redos[self.raster.id()].append(data)
        else:
            return
        self.change_cell_value(data[0], data[2], data[3])

    def redo(self):
        if self.redos[self.raster.id()]:
            data = self.redos[self.raster.id()].pop()
            self.undos[self.raster.id()].append(data)
        else:
            return
        self.change_cell_value(data[1], data[2], data[3])

    def define_nodata(self):
        """Define and write a new NoData value to raster file"""
        if not self.raster:
            self.uc.bar_warn('Select a raster layer to define/change NoData value!')
            return
        
        # check if user defined additional NODATA value
        if self.rdp.userNoDataValues(1):
            note = '\nNote: there is a user defined NODATA value.\nCheck the raster properties (Transparency).'
        else:
            note = ''
        # first band data type
        dt = self.rdp.dataType(1)
        
        # current NODATA value
        if self.rdp.sourceHasNoDataValue(1):
            cur_nodata = self.rdp.sourceNoDataValue(1)
            if dt < 6:
                cur_nodata = '{0:d}'.format(int(float(cur_nodata)))
        else:
            cur_nodata = ''
        
        label = 'Define/change raster NODATA value.\n\n'
        label += 'Raster data type: {}.{}'.format(dtypes[dt]['name'], note)
        nd, ok = QInputDialog.getText(None, "Define NODATA Value",
            label, QLineEdit.Normal, str(cur_nodata))
        if not ok:
            return
        
        if not is_number(nd):
            self.uc.show_warn('Wrong NODATA value!')
            return
        
        new_nodata = int(nd) if dt < 6 else float(nd)
        
        # set the NODATA value for each band
        res = []
        for nr in range(1, min(4, self.band_count + 1)):
            res.append(self.rdp.setNoDataValue(nr, new_nodata))
            self.rdp.sourceHasNoDataValue(nr)
        
        if False in res:
            self.uc.show_warn('Setting new NODATA value failed!')
        else:
            self.uc.bar_info('Succesful setting new NODATA values!', dur=2)

        self.prepare_raster()
        self.raster.triggerRepaint()
        
    def check_undo_redo_btns(self):
        """Enable/Disable undo and redo buttons based on availability of undo/redo steps"""
        try:
            if len(self.undos[self.raster.id()]) == 0:
                self.undo_btn.setDisabled(True)
            else:
                self.undo_btn.setEnabled(True)
        except:
            self.undo_btn.setDisabled(True)
             
        try:
            if len(self.redos[self.raster.id()]) == 0:
                self.redo_btn.setDisabled(True)
            else:
                self.redo_btn.setEnabled(True)
        except:
            self.redo_btn.setDisabled(True)

    def disable_toolbar_actions(self):
        # disable all toolbar actions but Help (for vectors and unsupported rasters)
        for action in self.actions:
            action.setDisabled(True)
        self.show_help.setEnabled(True)
    
    def check_layer(self, layer):
        """Check if we can work with the raster"""
        if layer == None \
                or not layer.isValid() \
                or not layer.type() == raster_layer_type \
                or not (layer.dataProvider().capabilities() & QgsRasterDataProvider.Create) \
                or layer.crs() is None:
            return False
        else:
            return True

    def set_active_raster(self):
        """Active layer has change - check if it is a raster layer and prepare it for the plugin"""

        if self.bands:
            self.bands = None

        for sbox in self.sboxes:
            sbox.setValue(0)

        layer = self.iface.activeLayer()

        if self.check_layer(layer):
            self.raster = layer
            self.rdp = layer.dataProvider()
            self.band_count = layer.bandCount()
            
            # is data type supported?
            supported = True
            for nr in range(1, min(4, self.band_count + 1)):
                if self.rdp.dataType(nr) == 0 or self.rdp.dataType(nr) > 7:
                    t = dtypes[self.rdp.dataType(nr)]['name']
                    supported = False
                
            if supported:
                # enable all toolbar actions
                for action in self.actions:
                    action.setEnabled(True)
                # if raster properties change, get them (refeshes view)
                self.raster.rendererChanged.connect(self.prepare_raster)

                self.prepare_raster(supported)

            # not supported data type
            else:
                msg = 'The raster data type is: {}.'.format(t)
                msg += '\nServal can\'t work with it, sorry!'
                self.uc.show_warn(msg)
                self.reset_raster()
        
        # it is not a supported raster layer
        else:
            self.reset_raster()

        self.check_undo_redo_btns()

    def reset_raster(self):
        self.raster = None
        self.mColorButton.setDisabled(True)
        self.prepare_raster(False)

    def prepare_raster(self, supported=True):
        """Open raster using GDAL if it is supported"""

        # reset bands' spin boxes
        for i, sbox in enumerate(self.sboxes):
            sbox.setProperty('bandNr', i + 1)
            sbox.setDisabled(True)
            
        if not supported:
            return

        if self.band_count > 2:
            self.mColorButton.setEnabled(True)
        else:
            self.mColorButton.setDisabled(True)

        extent = self.raster.extent()
        self.rbounds = extent.toRectF().getCoords()

        self.bands = {}
        for nr in range(1, min(4, self.band_count + 1)):
            self.bands[nr] = {}
            self.bands[nr]['sbox'] = self.sboxes[nr - 1]

            # NODATA
            if self.rdp.sourceHasNoDataValue(nr): # source nodata value?
                self.bands[nr]['nodata'] = self.rdp.sourceNoDataValue(nr)
                # use the src nodata
                self.rdp.setUseSourceNoDataValue(nr, True)
            # no nodata defined in the raster source
            else:
                # check if user defined any nodata values
                if self.rdp.userNoDataValues(nr):
                    # get min nodata value from the first user nodata range
                    nd_ranges = self.rdp.userNoDataValues(nr)
                    self.bands[nr]['nodata'] = nd_ranges[0].min()
                else:
                    # leave nodata undefined
                    self.bands[nr]['nodata'] = None

            # enable band's spin box
            self.bands[nr]['sbox'].setEnabled(True)
            # get bands data type
            dt = self.bands[nr]['qtype'] = self.rdp.dataType(nr)
            # set spin boxes properties
            self.bands[nr]['sbox'].setMinimum(dtypes[dt]['min'])
            self.bands[nr]['sbox'].setMaximum(dtypes[dt]['max'])
            self.bands[nr]['sbox'].setDecimals(dtypes[dt]['dig'])

    @staticmethod
    def show_website():
        QDesktopServices.openUrl(QUrl('https://github.com/erpas/serval/wiki'))
