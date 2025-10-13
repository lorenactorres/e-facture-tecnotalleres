# -*- coding: utf-8 -*-
from odoo import _, api, fields, models, tools, Command
from cryptography.hazmat.primitives import hashes, serialization
from pytz import timezone
from odoo.exceptions import UserError,ValidationError
from odoo.tools import float_repr,cleanup_xml_node
from odoo.tools.float_utils import float_round
from collections import defaultdict
import hashlib
from lxml import etree
import xml.etree.ElementTree as ET
import re
from markupsafe import Markup
from base64 import encodebytes, b64encode
import io
import zipfile
from odoo.tools import html_escape
from . import xml_utils
from odoo.exceptions import UserError
from hashlib import sha384
import base64
import logging
import html
from random import randint
import qrcode
from io import BytesIO
_logger = logging.getLogger(__name__)
COUNTRIES_ES = {
    "AF": "Afganistán",
    "AX": "Åland",
    "AL": "Albania",
    "DE": "Alemania",
    "AD": "Andorra",
    "AO": "Angola",
    "AI": "Anguila",
    "AQ": "Antártida",
    "AG": "Antigua y Barbuda",
    "SA": "Arabia Saudita",
    "DZ": "Argelia",
    "AR": "Argentina",
    "AM": "Armenia",
    "AW": "Aruba",
    "AU": "Australia",
    "AT": "Austria",
    "AZ": "Azerbaiyán",
    "BS": "Bahamas",
    "BD": "Bangladés",
    "BB": "Barbados",
    "BH": "Baréin",
    "BE": "Bélgica",
    "BZ": "Belice",
    "BJ": "Benín",
    "BM": "Bermudas",
    "BY": "Bielorrusia",
    "BO": "Bolivia",
    "BQ": "Bonaire, San Eustaquio y Saba",
    "BA": "Bosnia y Herzegovina",
    "BW": "Botsuana",
    "BR": "Brasil",
    "BN": "Brunéi",
    "BG": "Bulgaria",
    "BF": "Burkina Faso",
    "BI": "Burundi",
    "BT": "Bután",
    "CV": "Cabo Verde",
    "KH": "Camboya",
    "CM": "Camerún",
    "CA": "Canadá",
    "QA": "Catar",
    "TD": "Chad",
    "CL": "Chile",
    "CN": "China",
    "CY": "Chipre",
    "CO": "Colombia",
    "KM": "Comoras",
    "KP": "Corea del Norte",
    "KR": "Corea del Sur",
    "CI": "Costa de Marfil",
    "CR": "Costa Rica",
    "HR": "Croacia",
    "CU": "Cuba",
    "CW": "Curazao",
    "DK": "Dinamarca",
    "DM": "Dominica",
    "EC": "Ecuador",
    "EG": "Egipto",
    "SV": "El Salvador",
    "AE": "Emiratos Árabes Unidos",
    "ER": "Eritrea",
    "SK": "Eslovaquia",
    "SI": "Eslovenia",
    "ES": "España",
    "US": "Estados Unidos",
    "EE": "Estonia",
    "ET": "Etiopía",
    "PH": "Filipinas",
    "FI": "Finlandia",
    "FJ": "Fiyi",
    "FR": "Francia",
    "GA": "Gabón",
    "GM": "Gambia",
    "GE": "Georgia",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GD": "Granada",
    "GR": "Grecia",
    "GL": "Groenlandia",
    "GP": "Guadalupe",
    "GU": "Guam",
    "GT": "Guatemala",
    "GF": "Guayana Francesa",
    "GG": "Guernsey",
    "GN": "Guinea",
    "GW": "Guinea-Bisáu",
    "GQ": "Guinea Ecuatorial",
    "GY": "Guyana",
    "HT": "Haití",
    "HN": "Honduras",
    "HK": "Hong Kong",
    "HU": "Hungría",
    "IN": "India",
    "ID": "Indonesia",
    "IQ": "Irak",
    "IR": "Irán",
    "IE": "Irlanda",
    "BV": "Isla Bouvet",
    "IM": "Isla de Man",
    "CX": "Isla de Navidad",
    "IS": "Islandia",
    "KY": "Islas Caimán",
    "CC": "Islas Cocos",
    "CK": "Islas Cook",
    "FO": "Islas Feroe",
    "GS": "Islas Georgias del Sur y Sandwich del Sur",
    "HM": "Islas Heard y McDonald",
    "FK": "Islas Malvinas",
    "MP": "Islas Marianas del Norte",
    "MH": "Islas Marshall",
    "PN": "Islas Pitcairn",
    "SB": "Islas Salomón",
    "TC": "Islas Turcas y Caicos",
    "UM": "Islas ultramarinas de Estados Unidos",
    "VG": "Islas Vírgenes Británicas",
    "VI": "Islas Vírgenes de los Estados Unidos",
    "IL": "Israel",
    "IT": "Italia",
    "JM": "Jamaica",
    "JP": "Japón",
    "JE": "Jersey",
    "JO": "Jordania",
    "KZ": "Kazajistán",
    "KE": "Kenia",
    "KG": "Kirguistán",
    "KI": "Kiribati",
    "XK": "Kosovo",
    "KW": "Kuwait",
    "LA": "Laos",
    "LS": "Lesoto",
    "LV": "Letonia",
    "LB": "Líbano",
    "LR": "Liberia",
    "LY": "Libia",
    "LI": "Liechtenstein",
    "LT": "Lituania",
    "LU": "Luxemburgo",
    "MO": "Macao",
    "MK": "Macedonia",
    "MG": "Madagascar",
    "MY": "Malasia",
    "MW": "Malaui",
    "MV": "Maldivas",
    "ML": "Malí",
    "MT": "Malta",
    "MA": "Marruecos",
    "MQ": "Martinica",
    "MU": "Mauricio",
    "MR": "Mauritania",
    "YT": "Mayotte",
    "MX": "México",
    "FM": "Micronesia",
    "MD": "Moldavia",
    "MC": "Mónaco",
    "MN": "Mongolia",
    "ME": "Montenegro",
    "MS": "Montserrat",
    "MZ": "Mozambique",
    "MM": "Myanmar",
    "NA": "Namibia",
    "NR": "Nauru",
    "NP": "Nepal",
    "NI": "Nicaragua",
    "NE": "Níger",
    "NG": "Nigeria",
    "NU": "Niue",
    "NF": "Norfolk",
    "NO": "Noruega",
    "NC": "Nueva Caledonia",
    "NZ": "Nueva Zelanda",
    "OM": "Omán",
    "NL": "Países Bajos",
    "PK": "Pakistán",
    "PW": "Palaos",
    "PS": "Palestina",
    "PA": "Panamá",
    "PG": "Papúa Nueva Guinea",
    "PY": "Paraguay",
    "PE": "Perú",
    "PF": "Polinesia Francesa",
    "PL": "Polonia",
    "PT": "Portugal",
    "PR": "Puerto Rico",
    "GB": "Reino Unido",
    "EH": "República Árabe Saharaui Democrática",
    "CF": "República Centroafricana",
    "CZ": "República Checa",
    "CG": "República del Congo",
    "CD": "República Democrática del Congo",
    "DO": "República Dominicana",
    "RE": "Reunión",
    "RW": "Ruanda",
    "RO": "Rumania",
    "RU": "Rusia",
    "WS": "Samoa",
    "AS": "Samoa Americana",
    "BL": "San Bartolomé",
    "KN": "San Cristóbal y Nieves",
    "SM": "San Marino",
    "MF": "San Martín",
    "PM": "San Pedro y Miquelón",
    "VC": "San Vicente y las Granadinas",
    "SH": "Santa Elena, Ascensión y Tristán de Acuña",
    "LC": "Santa Lucía",
    "ST": "Santo Tomé y Príncipe",
    "SN": "Senegal",
    "RS": "Serbia",
    "SC": "Seychelles",
    "SL": "Sierra Leona",
    "SG": "Singapur",
    "SX": "Sint Maarten",
    "SY": "Siria",
    "SO": "Somalia",
    "LK": "Sri Lanka",
    "SZ": "Suazilandia",
    "ZA": "Sudáfrica",
    "SD": "Sudán",
    "SS": "Sudán del Sur",
    "SE": "Suecia",
    "CH": "Suiza",
    "SR": "Surinam",
    "SJ": "Svalbard y Jan Mayen",
    "TH": "Tailandia",
    "TW": "Taiwán (República de China)",
    "TZ": "Tanzania",
    "TJ": "Tayikistán",
    "IO": "Territorio Británico del Océano Índico",
    "TF": "Tierras Australes y Antárticas Francesas",
    "TL": "Timor Oriental",
    "TG": "Togo",
    "TK": "Tokelau",
    "TO": "Tonga",
    "TT": "Trinidad y Tobago",
    "TN": "Túnez",
    "TM": "Turkmenistán",
    "TR": "Turquía",
    "TV": "Tuvalu",
    "UA": "Ucrania",
    "UG": "Uganda",
    "UY": "Uruguay",
    "UZ": "Uzbekistán",
    "VU": "Vanuatu",
    "VA": "Vaticano, Ciudad del",
    "VE": "Venezuela",
    "VN": "Vietnam",
    "WF": "Wallis y Futuna",
    "YE": "Yemen",
    "DJ": "Yibuti",
    "ZM": "Zambia",
    "ZW": "Zimbabue",
}

tipo_ambiente = {
    "PRODUCCION": "1",
    "PRUEBA": "2",
}

class DianDocument(models.Model):
    _inherit = "dian.document"
    message_json = fields.Json()
    message = fields.Html(compute="_compute_message")
    invoice_id = fields.Many2one(comodel_name='ir.attachment', string="XML Factura")
    response_id = fields.Many2one(comodel_name='ir.attachment', string="Respuesta DIAN")
    attachment_id = fields.Many2one(comodel_name='ir.attachment', string="Attached DIAN")
 
    
    @api.depends('message_json')
    def _compute_message(self):
        for doc in self:
            if not doc.message_json or not isinstance(doc.message_json, dict):
                doc.message = "No hay información de mensaje disponible"
                continue

            msg = html_escape(doc.message_json.get('status', ""))
            
            if doc.message_json.get('errors'):
                errors = doc.message_json['errors']
                if isinstance(errors, list):
                    error_list = Markup().join(
                        Markup("<li>%s</li>") % html_escape(error) for error in errors
                    )
                    msg += Markup("<ul>{errors}</ul>").format(errors=error_list)
                elif isinstance(errors, str):
                    msg += Markup("<ul><li>%s</li></ul>") % html_escape(errors)
                else:
                    msg += Markup("<ul><li>Error desconocido</li></ul>")

            doc.message = msg

    @api.model
    def _parse_errors(self, root):
        """ Returns a list containing the errors/warnings from a DIAN response """
        return [node.text for node in root.findall(".//{*}ErrorMessage/{*}string")]

    @api.model
    def _build_message(self, root):
        msg = {'status': False, 'errors': []}
        fault = root.find('.//{*}Fault/{*}Reason/{*}Text')
        if fault is not None and fault.text:
            msg['status'] = fault.text + " (Esto podría deberse al uso de certificados incorrectos.)"
        status = root.find('.//{*}StatusDescription')
        if status is not None and status.text:
            msg['status'] = status.text
        msg['errors'] = self._parse_errors(root)
        return msg

    def _action_get__xml(self,name=False,cufe=False):
        """ Fetch the status of a document sent to 'SendTestSetAsync' using the 'GetStatusZip' webservice. """
        self.ensure_one()
        if not cufe:
            cufe = self.cufe
            name = f'DIAN_invoice_.xml'
        response = xml_utils._build_and_send_request(
            self,
            payload={
                'track_id': cufe,
                'soap_body_template': "l10n_co_e-invoice.get_xml",
            },
            service="GetXmlByDocumentKey",
            company=self.document_id.company_id,
        )
        
        if response['status_code'] == 200:
            root = etree.fromstring(response['response'])
            self.message_json = self._build_message(root)
            namespaces = {
                's': 'http://www.w3.org/2003/05/soap-envelope',
                'b': 'http://schemas.datacontract.org/2004/07/EventResponse'
            }
            code = root.xpath('//s:Body//b:Code/text()', namespaces=namespaces)
            message = root.xpath('//s:Body//b:Message/text()', namespaces=namespaces)
            xml_bytes_base64 = root.xpath('//s:Body//b:XmlBytesBase64/text()', namespaces=namespaces)
            if xml_bytes_base64:
                base64_content = xml_bytes_base64[0]   
                decoded_content = base64.b64decode(base64_content)
                attachment_vals = {
                    'name': name,
                    'type': 'binary',
                    'datas': base64.b64encode(decoded_content),
                    'res_model': self._name,
                    'res_id': self.id,
                }
                attachment = self.env['ir.attachment'].create(attachment_vals)
                self.write({'invoice_id': attachment.id, 'xml_document': decoded_content, })
        elif response['status_code']:
            raise UserError(_("El servidor de la DIAN arrojó error (Codigo %s)", response['status_code']))
        else:
            raise UserError(_("El servidor DIAN no respondió."))

    def _get_qr_co(self):
        """
        """
        self.ensure_one()
        root = etree.fromstring(self.invoice_id.raw)
        nsmap = {k: v for k, v in root.nsmap.items() if k}  # empty namespace prefix is not supported for XPaths
        supplier_company_id = root.findtext('./cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID', namespaces=nsmap)
        customer_company_id = root.findtext('./cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID', namespaces=nsmap)
        line_extension_amount = root.findtext('./cac:LegalMonetaryTotal/cbc:LineExtensionAmount', namespaces=nsmap)
        tax_amount_01 = sum(float(x) for x in root.xpath('./cac:TaxTotal[.//cbc:ID/text()="01"]/cbc:TaxAmount/text()', namespaces=nsmap))
        payable_amount = root.findtext('./cac:LegalMonetaryTotal/cbc:PayableAmount', namespaces=nsmap)
        identifier = root.findtext('./cbc:UUID', namespaces=nsmap)
        qr_code = root.findtext('./ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/sts:DianExtensions/sts:QRCode', namespaces=nsmap)
        vals = {
            'NumDS': root.findtext('./cbc:ID', namespaces=nsmap),
            'FecFD': root.findtext('./cbc:IssueDate', namespaces=nsmap),
            'HorDS': root.findtext('./cbc:IssueTime', namespaces=nsmap),
        }
        if self.move_type in ('in_invoice', 'in_refund'):
            vals.update({
                'NumSNO': supplier_company_id,
                'DocABS': customer_company_id,
                'ValDS': line_extension_amount,
                'ValIva': tax_amount_01,
                'ValTolDS': payable_amount,
                'CUDS': identifier,
                'QRCode': qr_code,
            })
        else:
            vals.update({
                'NitFac': supplier_company_id,
                'DocAdq': customer_company_id,
                'ValFac': line_extension_amount,
                'ValIva': tax_amount_01,
                'ValOtroIm': sum(float(x) for x in root.xpath('./cac:TaxTotal[.//cbc:ID/text()!="01"]/cbc:TaxAmount/text()', namespaces=nsmap)),
                'ValTolFac': payable_amount,
                'CUFE': identifier,
                'QRCode': qr_code,
            })
        qr_code_text = "\n".join(f"{k}: {v}" for k, v in vals.items())
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_code_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        # Convertir la imagen a base64
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        qr_code_image = base64.b64encode(buffered.getvalue()).decode()

        return qr_code_text, qr_code_image

    def generate_and_save_qr_code(self):
        for record in self:
            qr_code_text, qr_code_image = record._l10n_co_dian_get_invoice_report_qr_code_value()
            record.write({
                'QR_code': qr_code_image,
                'qr_data': qr_code_text,
            })

    def format_float(self, amount, precision_digits):
        if amount is None:
            return None
        return float_repr(float_round(amount, precision_digits), precision_digits)