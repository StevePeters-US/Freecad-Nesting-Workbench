# Nesting/nesting/spreadsheet_utils.py

"""
This module contains utility functions for creating and managing the
layout parameters spreadsheet in FreeCAD.
"""

import FreeCAD

def create_layout_spreadsheet(doc, group, ui_params, sheet_efficiencies=None):
    """
    Creates and populates a spreadsheet with layout parameters and efficiencies.

    Args:
        doc (FreeCAD.Document): The active document.
        group (App.DocumentObjectGroup): The main layout group to add the spreadsheet to.
        ui_params (dict): A dictionary of parameters from the UI.
        sheet_efficiencies (list, optional): A list of fill percentages for each sheet.
    """
    try:
        import Spreadsheet
    except ImportError:
        FreeCAD.Console.PrintWarning("Spreadsheet workbench is not available. Cannot create parameters sheet.\n")
        return

    sheet_data = doc.addObject("Spreadsheet::Sheet", "LayoutParameters")
    sheet_data.set('A1', 'Parameter')
    sheet_data.set('B1', 'Value')
    sheet_data.set('A2', 'SheetWidth')
    sheet_data.set('B2', str(ui_params.get('sheet_w', 0)))
    sheet_data.set('A3', 'SheetHeight')
    sheet_data.set('B3', str(ui_params.get('sheet_h', 0)))
    sheet_data.set('A4', 'PartSpacing')
    sheet_data.set('B4', str(ui_params.get('spacing', 0)))
    sheet_data.set('A5', 'MaterialThickness')
    sheet_data.set('B5', str(ui_params.get('material_thickness', 3.0)))
    sheet_data.set('A6', 'FontFile')
    sheet_data.set('B6', ui_params.get('font_path', ''))

    if sheet_efficiencies:
        sheet_data.set('A7', '--- Sheet Efficiencies ---')
        for i, efficiency in enumerate(sheet_efficiencies):
            sheet_data.set(f'A{8+i}', f'Sheet {i+1} Efficiency (%)')
            sheet_data.set(f'B{8+i}', f'{efficiency:.2f}')

    group.addObject(sheet_data)