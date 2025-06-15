# services/sheets_service.py

import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

class SheetsService:
    def __init__(self, service_account_file: str, spreadsheet_id: str):
        """
        service_account_file: ruta al JSON de la cuenta de servicio
        spreadsheet_id: ID de la hoja de cálculo
        """
        scopes = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_file(
            service_account_file,
            scopes=scopes
        )
        self.client = build('sheets', 'v4', credentials=creds)
        self.spreadsheet_id = spreadsheet_id

    def get_values(self, range_name: str):
        sheet = self.client.spreadsheets()
        result = sheet.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=range_name
        ).execute()
        return result.get('values', [])

    def update_values(self, range_name: str, values: list):
        body = {'values': values}
        sheet = self.client.spreadsheets()
        result = sheet.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            valueInputOption='RAW',
            body=body
        ).execute()
        return result
    