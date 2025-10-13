# -*- coding: utf-8 -*-
import datetime
from datetime import timedelta, date
import hashlib
import logging
import pyqrcode
import zipfile
import pytz
import json
from unidecode import unidecode
from collections import defaultdict
from contextlib import contextmanager
from functools import lru_cache
from odoo import api, fields, models, Command, _
import base64
from odoo.tools.misc import formatLang, format_date, get_lang, groupby
from odoo.exceptions import AccessError, UserError, RedirectWarning, ValidationError
from lxml import etree
from io import BytesIO
from xml.sax import saxutils
import xml.etree.ElementTree as ET
import html
_logger = logging.getLogger(__name__)
urllib3_logger = logging.getLogger('urllib3')
urllib3_logger.setLevel(logging.ERROR)
from . import global_functions
from pytz import timezone
from requests import post, exceptions
from lxml import etree
from odoo import models, fields, _, api
import logging
_logger = logging.getLogger(__name__)
import unicodedata
from odoo.tools.image import image_data_uri
import ssl
from decimal import Decimal, ROUND_HALF_UP
from odoo.tools import convert_file, html2plaintext, is_html_empty
ssl._create_default_https_context = ssl._create_unverified_context
DIAN = {'wsdl-hab': 'https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc?wsdl',
        'wsdl': 'https://vpfe.dian.gov.co/WcfDianCustomerServices.svc?wsdl',
        'catalogo-hab': 'https://catalogo-vpfe-hab.dian.gov.co/Document/FindDocument?documentKey={}&partitionKey={}&emissionDate={}',
        'catalogo': 'https://catalogo-vpfe.dian.gov.co/Document/FindDocument?documentKey={}&partitionKey={}&emissionDate={}'}

TYPE_DOC_NAME = {
    'invoice': _('Invoice'),
    'credit': _('Credit Note'),
    'debit': _('Debit Note')
}

EDI_OPERATION_TYPE = [
    ('10', 'Estandar'),
    ('09', 'AIU'),
    ('11', 'Mandatos'),
]

EVENT_CODES = [
    ('02', '[02] Documento validado por la DIAN'),
    ('04', '[03] Documento rechazado por la DIAN'),
    ('030', '[030] Acuse de recibo'),
    ('031', '[031] Reclamo'),
    ('032', '[032] Recibo del bien'),
    ('033', '[033] Aceptación expresa'),
    ('034', '[034] Aceptación Tácita'),
    ('other', 'Otro')
]


class Invoice(models.Model):
    _inherit = "account.move"

          
    fecha_envio = fields.Datetime(string='Fecha de envío en UTC',copy=False)
    fecha_entrega = fields.Datetime(string='Fecha de entrega',copy=False)
    fecha_xml = fields.Datetime(string='Fecha de factura Publicada',copy=False)
    total_withholding_amount = fields.Float(string='Total de retenciones')
    invoice_trade_sample = fields.Boolean(string='Tiene muestras comerciales',)
    receipt = fields.Boolean(string='Tiene ordenes de entrega?',)
    trade_sample_price = fields.Selection([('01', 'Valor comercial')],   string='Referencia a precio real',  )
    application_response_ids = fields.One2many('dian.application.response','move_id')
    get_status_event_status_code = fields.Selection([('00', 'Procesado Correctamente'),
                                                   ('66', 'NSU no encontrado'),
                                                   ('90', 'TrackId no encontrado'),
                                                   ('99', 'Validaciones contienen errores en campos mandatorios'),
                                                   ('other', 'Other')], string='StatusCode', default=False)
    get_status_event_response = fields.Text(string='Response')
    response_message_dian = fields.Text(string='Response Dian')
    response_eve_dian = fields.Text(string='Response Dian')
    message_error_DIAN_event = fields.Text(string='Response Dian error')
    receipts = fields.One2many("receipt.code","move_id", string="Codigo de entrega")
    titulo_state = fields.Selection([
        ('grey', 'No Titulo Valor'),
        ('red', 'Proceso'),
        ('green', 'Titulo Valor')], string='Titulo Valor', default='grey')

    fe_type = fields.Selection(
        [('01', 'Factura de venta'),
         ('02', 'Factura de exportación'),
         ('03', 'Documento electrónico de transmisión - tipo 03'),
         ('04', 'Factura electrónica de Venta - tipo 04'), 
         ],
        'Tipo De Factura Electronica',
        required=False,
        default='01',
        readonly=True,
    )
    fe_type_ei_ref = fields.Selection(
        [('01', 'Factura de venta'),
         ('02', 'Factura de exportación'),
        # ('03', 'Documento electrónico de transmisión - tipo 03'),
         #('04', 'Factura electrónica de Venta - tipo 04'),
         ('91', 'Nota Crédito'),
         ('92', 'Nota Débito'),
         ('96', 'Eventos (ApplicationResponse)'), ],
        'Tipo de Documento Electronico',
        required=False,
        readonly=True,
        compute='_type_ei_default',
        
    )
    fe_operation_type = fields.Selection(EDI_OPERATION_TYPE,
                                         'Tipo de Operacion',
                                         default='10',
                                         required=True)
    supplier_claim_concept = fields.Selection(
        [
            ('01', 'Documento con inconsistencias'),
            ('02', 'Mercancia no entregada totalmente'),
            ('03', 'Mercancia no entregada parcialmente'),
            ('04', 'Servicio no prestado'),
        ],
        string="Concepto de Reclamo", tracking=True)
    zip_file = fields.Binary('Archivo Zip')
    zip_file_name = fields.Char('File name')
    xml_text = fields.Text('Contenido XML')
    invoice_xml = fields.Text('Factura XML')
    credit_note_count = fields.Integer('# NC', compute='_compute_credit_count')

    def send_dian_document_new(self):
        for rec in self:
            rec.diancode_id.unlink()
            document_dian = rec.diancode_id
            if not document_dian and rec.state == "posted":
                if rec.move_type in ("out_invoice", "in_invoice") and not rec.is_debit_note:
                    document_dian = self.env["dian.document"].sudo().create({"document_id": rec.id, "document_type": "f"})
                elif rec.move_type in ("out_refund", "in_refund"):
                    document_dian = self.env["dian.document"].sudo().create({"document_id": rec.id, "document_type": "c"})
                elif rec.move_type in ("out_invoice", "in_invoice") and rec.debit_origin_id:
                    document_dian = self.env["dian.document"].sudo().create({"document_id": rec.id, "document_type": "d"})
            rec.diancode_id = document_dian.id
            document_type = document_dian.document_type
            document_dian.send_pending_dian(document_dian, document_type,rec)
        return True


    def _get_einv_warning(self):
        warn_remaining = False
        inactive_resolution = False
        sequence_id = self.journal_id.sequence_id

        if sequence_id.use_dian_control:
            remaining_numbers = max(5,sequence_id.remaining_numbers)
            remaining_days = max(5,sequence_id.remaining_days)
            date_range = self.env['ir.sequence.dian_resolution'].search(
                [('sequence_id', '=', sequence_id.id),
                 ('active_resolution', '=', True)])
            today = datetime.datetime.strptime(
                str(fields.Date.today(self)),
                '%Y-%m-%d'
            )
            if date_range:
                date_range.ensure_one()
                date_to = datetime.datetime.strptime(
                    str(date_range.date_to),
                    '%Y-%m-%d'
                )
                days = (date_to - today).days
                numbers = date_range.number_to - self.sequence_number
                if numbers < remaining_numbers or days < remaining_days:
                    warn_remaining = True
            else:
                inactive_resolution = True
        self.is_inactive_resolution = inactive_resolution
        self.fe_warning = warn_remaining

    fe_warning = fields.Boolean('¿Advertir por rangos de resolución?',
                                compute='_get_einv_warning',
                                store=False)
    is_inactive_resolution = fields.Boolean('¿Advertir resolución inactiva?',
                                            compute='_get_einv_warning',
                                            store=False)

    last_event_status = fields.Char(string="Último evento exitoso", compute="_compute_last_event_status")

    @api.depends('application_response_ids.status', 'application_response_ids.response_code')
    def _compute_last_event_status(self):
        for record in self:
            last_successful_event = record.application_response_ids.filtered(lambda r: r.status == 'exitoso').sorted(key=lambda r: r.create_date, reverse=True)
            record.last_event_status = last_successful_event[0].response_code if last_successful_event else False

    @api.depends('reversal_move_id')
    def _compute_credit_count(self):
        credit_data = self.env['account.move'].read_group(
            [('reversed_entry_id', 'in', self.ids)],
            ['reversed_entry_id'],
            ['reversed_entry_id']
        )
        data_map = {
            datum['reversed_entry_id'][0]:
            datum['reversed_entry_id_count'] for datum in credit_data
        }
        for inv in self:
            inv.credit_note_count = data_map.get(inv.id, 0.0)

    def action_view_credit_notes(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit Notes'),
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('reversed_entry_id', '=', self.id)],
        }

    @api.depends('move_type','partner_id')
    def _type_ei_default(self):
        for rec in self:
            if rec.move_type in ('out_invoice','in_invoice') and not rec.is_debit_note:
                rec.fe_type_ei_ref = '01'
            elif rec.move_type in ('out_invoice','in_invoice') and rec.is_debit_note:
                rec.fe_type_ei_ref =  '92'
            elif rec.move_type in ('out_refund','in_refund'):
                rec.fe_type_ei_ref =  '91'  
            else:
                rec.fe_type_ei_ref =  '01'
    
    def validate_event(self):
        sql = """SELECT am.id 
                FROM account_move am
                WHERE am.titulo_state != 'green' 
                    AND am.move_type = 'out_invoice'
                    AND am.state = 'posted';"""
        self.env.cr.execute(sql)
        sql_result = self.env.cr.dictfetchall()

        # Crear lotes de 40 registros cada uno
        batch_size = 40
        for i in range(0, len(sql_result), batch_size):
            batch = sql_result[i:i + batch_size]
            inv_to_validate_dian = (
                self.env["account.move"].sudo().browse([n.get("id") for n in batch])
            )

            # Procesar cada registro en el lote
            for idian in inv_to_validate_dian:
                try:
                    # Creando un punto de guardado
                    with self.env.cr.savepoint():
                        idian.action_GetStatusevent()
                except Exception as e:
                    _logger.info(f"Error procesando el registro {idian.name}: {e}")


    def action_send_and_print(self):
        template = self.env.ref('l10n_co_e-invoice.email_template_edi_invoice_dian', raise_if_not_found=False)
        dian_constants = self.diancode_id._generate_dian_constants(self, self.move_type, False)  #self.diancode_id._get_dian_constants(self)
        xml, name_xml = self.diancode_id.enviar_email_attached_document_xml(
            self.diancode_id.xml_response_dian,
            dian_document=self.diancode_id,
            dian_constants=dian_constants,
            data_header_doc=self,
        )
        zip_file_name = name_xml.split(".")[0]
        # Create a ZIP file containing XML and PDF files
        with BytesIO() as zip_buffer:
            with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED) as zip_file:
                # Ensure that xml is a bytes object before writing it to the ZIP file
                #xml_bytes = base64.b64decode().decode('utf-8')
                zip_file.writestr(name_xml, xml)

                pdf_file_name = zip_file_name + ".pdf"
                pdf_content = self.env['ir.actions.report'].sudo()._render_qweb_pdf("account.account_invoices", self.id)[0]
                zip_file.writestr(pdf_file_name, pdf_content)

            # Get the ZIP content as bytes
            zip_content = zip_buffer.getvalue()
        zip_base64 = base64.b64encode(zip_content).decode()
        dict_adjunto = {
            "res_id": self.id,
            "res_model": "account.move",
            "type": "binary",
            "name": zip_file_name + ".zip",
            "datas": zip_base64,
        }
        if template:
            template.sudo().attachment_ids = [(5, 0, [])]
            template.sudo().attachment_ids = [(0, 0, dict_adjunto)]
        # Encode the ZIP content in base64


        if any(not x.is_sale_document(include_receipts=True) for x in self):
            raise UserError(_("You can only send sales documents"))

        return {
            'name': _("Send"),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'account.move.send',
            'target': 'new',
            'context': {
                'active_ids': self.ids,
                'default_mail_template_id': template and template.id or False,
            },
        }


    def action_invoice_sent_2(self):
        if self.company_id.production:
            for rec in self:
                dian_constants = rec.diancode_id._get_dian_constants(rec)
                response = rec.diancode_id.xml_response_dian
                
                try:
                    root = ET.fromstring(response)
                    if root.tag == '{http://www.w3.org/2003/05/soap-envelope}Envelope':
                        xml, name_xml = rec.diancode_id.enviar_email_attached_document_xml(
                            response,
                            dian_document=rec.diancode_id,
                            dian_constants=dian_constants,
                            data_header_doc=rec,
                        )
                    else:
                        raise ValueError("XML structure is not as expected")
                except (ET.ParseError, ValueError):
                    response_attachment = rec.diancode_id.response_id
                    if not response_attachment:
                        raise UserError(_("No valid DIAN response found. Please verify the invoice status."))
                    
                    response_xml_escaped = base64.b64decode(response_attachment.datas).decode('UTF-8')
                    response_xml = html.unescape(response_xml_escaped)
                    response_root = ET.fromstring(response_xml.encode('UTF-8'))
                    response = ET.tostring(response_root, encoding='UTF-8').decode('UTF-8')
                    _logger.error(response)
                    xml, name_xml = rec.diancode_id.enviar_email_attached_document_fe_xml(
                        response,
                        dian_document=rec.diancode_id,
                        dian_constants=dian_constants,
                        data_header_doc=rec,
                    )

                zip_file_name = name_xml.split(".")[0]
                
                with BytesIO() as zip_buffer:
                    with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED) as zip_file:
                        zip_file.writestr(name_xml, xml)
                        pdf_file_name = zip_file_name + ".pdf"
                        pdf_content = self.env['ir.actions.report'].sudo()._render_qweb_pdf("account.account_invoices", rec.id)[0]
                        zip_file.writestr(pdf_file_name, pdf_content)
                    
                    zip_content = zip_buffer.getvalue()
                
                zip_base64 = base64.b64encode(zip_content).decode()
                template = self.env.ref('l10n_co_e-invoice.email_template_edi_invoice_dian', raise_if_not_found=False)
                lang = self.env.lang
                if template and template.lang:
                    lang = template._render_template(template.lang, 'account.move', rec.ids)
                
                compose_form = self.env.ref('account.account_invoice_send_wizard_form', raise_if_not_found=False)
                ctx = dict(
                    default_model='account.move',
                    default_res_id=rec.id,
                    default_res_model='account.move',
                    default_use_template=bool(template),
                    default_template_id=template and template.id or False,
                    default_composition_mode='comment',
                    mark_invoice_as_sent=True,
                    default_email_layout_xmlid="mail.mail_notification_layout_with_responsible_signature",
                    model_description=rec.with_context(lang=lang).type_name,
                    force_email=True,
                    active_ids=rec.ids,
                )
                
                dict_adjunto = {
                    "res_id": rec.id,
                    "res_model": "account.move",
                    "type": "binary",
                    "name": zip_file_name + ".zip",
                    "datas": zip_base64,
                }
                if template:
                    template.sudo().attachment_ids = [(5, 0, [])]
                    template.sudo().attachment_ids = [(0, 0, dict_adjunto)]
                return {
                    'name': _('Send Invoice'),
                    'type': 'ir.actions.act_window',
                    'view_type': 'form',
                    'view_mode': 'form',
                    'res_model': 'account.invoice.send',
                    'views': [(compose_form.id, 'form')],
                    'view_id': compose_form.id,
                    'target': 'new',
                    'context': ctx,
                }
        else:
            return super(Invoice, self).action_invoice_sent()
   
   
    
    def dian_preview(self):
        for rec in self:
            if rec.cufe:
                return {
                    'type': 'ir.actions.act_url',
                    'target': 'new',
                    'url': 'https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=' + rec.cufe,
                }

    def dian_pdf_view(self):
        for rec in self:
            if rec.cufe:
                return {
                    'type': 'ir.actions.act_url',
                    'target': 'new',
                    'url': 'https://catalogo-vpfe.dian.gov.co/Document/DownloadPDF?trackId=' + rec.cufe,
                }

    def action_open_dian_page(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('dian.verification_page_url', 'https://catalogo-vpfe.dian.gov.co/document/searchqr')
        if not base_url:
            self.env['ir.config_parameter'].sudo().set_param('dian.verification_page_url', 'https://catalogo-vpfe.dian.gov.co/document/searchqr')
        return {
            'type': 'ir.actions.act_url',
            'url': f"{base_url}?documentkey={self.cufe_cuds_other_system}",
            'target': 'new',
        }

    @api.depends('application_response_ids')
    def _compute_titulo_state(self):
        kanban_state = 'grey'
        for rec in self:
            for event in rec.application_response_ids:
                if event.response_code in ('034','033') and event.status == "exitoso":
                    kanban_state = 'green'
            rec.titulo_state = kanban_state

    def add_application_response(self):
        for rec in self:
            response_code = rec._context.get('response_code')
            ar = self.env['dian.application.response'].generate_from_electronic_invoice(rec.id, response_code)


    def _get_GetStatus_values(self):
        xml_soap_values = global_functions.get_xml_soap_values(
            self.company_id.certificate_file,
            self.company_id.certificate_key)
        cufe = self.cufe or self.ei_uuid
        if self.move_type == "in_invoice":
            cufe = self.cufe_cuds_other_system
        xml_soap_values['trackId'] = cufe
        return xml_soap_values

    def action_GetStatus(self):
        wsdl = DIAN['wsdl-hab']
        if self.company_id.production:
            wsdl = DIAN['wsdl']
        GetStatus_values = self._get_GetStatus_values()
        GetStatus_values['To'] = wsdl.replace('?wsdl', '')
        xml_soap_with_signature = global_functions.get_xml_soap_with_signature(
            global_functions.get_template_xml(GetStatus_values, 'GetStatus'),
            GetStatus_values['Id'],
            self.company_id.certificate_file,
            self.company_id.certificate_key)
        response = post(
            wsdl,
            headers={'content-type': 'application/soap+xml;charset=utf-8'},
            data=etree.tostring(xml_soap_with_signature, encoding="unicode"))

        if response.status_code == 200:
            self._get_status_response(response,send_mail=False)
        else:
            raise ValidationError(response.status_code)

        return True

    def action_GetStatusevent(self):
        wsdl = DIAN['wsdl-hab']

        if self.company_id.production:
            wsdl = DIAN['wsdl']

        GetStatus_values = self._get_GetStatus_values()
        GetStatus_values['To'] = wsdl.replace('?wsdl', '')
        xml_soap_with_signature = global_functions.get_xml_soap_with_signature(
            global_functions.get_template_xml(GetStatus_values, 'GetStatusEvent'),
            GetStatus_values['Id'],
            self.company_id.certificate_file,
            self.company_id.certificate_key)

        response = post(
            wsdl,
            headers={'content-type': 'application/soap+xml;charset=utf-8'},
            data=etree.tostring(xml_soap_with_signature, encoding="unicode"))

        if response.status_code == 200:
            self._get_status_response(response,send_mail=False)
        else:
            raise ValidationError(response.status_code)

        return True

    def create_records_from_xml(self):
        if not hasattr(self, 'message_error_DIAN_event') or not self.message_error_DIAN_event:
            return
        ar = self.env['dian.application.response']
        xml_string = self.message_error_DIAN_event  # Your XML string
        xml_bytes = xml_string.encode('utf-8')  # Convert to bytes
        root = etree.fromstring(xml_bytes)
        document_responses = []
        titulo_value = 'grey'
        for doc_response in root.findall('.//cac:DocumentResponse', namespaces=root.nsmap):
            if doc_response.find('.//cbc:ResponseCode', namespaces=root.nsmap).text in ['034', '033']:
                titulo_value = 'green'
            response_data = {
                'response_code': doc_response.find('.//cbc:ResponseCode', namespaces=root.nsmap).text,
                'name': doc_response.find('.//cbc:Description', namespaces=root.nsmap).text,
                'issue_date': doc_response.find('.//cbc:EffectiveDate', namespaces=root.nsmap).text,
                'move_id': self.id,
                'status': "exitoso",
                'dian_get': True,
                'response_message_dian': 'Procesado Correctamente',
            }
            doc_reference = doc_response.find('.//cac:DocumentReference', namespaces=root.nsmap)
            response_data['number'] = doc_reference.find('.//cbc:ID', namespaces=root.nsmap).text
            response_data['cude'] = doc_reference.find('.//cbc:UUID', namespaces=root.nsmap).text
            existing_record = ar.search([('cude', '=', response_data['cude'])], limit=1)
            if not existing_record:
                document_responses.append(response_data)
            else:
                continue 
        if document_responses or doc_response:
            if document_responses:
                ar.create(document_responses)
            self.titulo_state = titulo_value


    def _get_status_response(self, response, send_mail):
        b = "http://schemas.datacontract.org/2004/07/DianResponse"
        c = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
        s = "http://www.w3.org/2003/05/soap-envelope"
        strings = ''
        to_return = True
        status_code = 'other'
        root = etree.fromstring(response.content)
        date_invoice = self.invoice_date
        root2 = etree.tostring(root, pretty_print=True).decode()
        if not date_invoice:
            date_invoice = fields.Date.today()

        for element in root.iter("{%s}StatusCode" % b):
            if element.text in ('0', '00', '66', '90', '99'):
                status_code = element.text
        if status_code == '0':
            self.action_GetStatus()
            return True
        if status_code == '00':
            for element in root.iter("{%s}StatusMessage" % b):
                strings = element.text
            for element in root.iter("{%s}XmlBase64Bytes" % b):
                self.write({'message_error_DIAN_event': base64.b64decode(element.text).decode('utf-8') })
            to_return = True
        else:
            if send_mail:
                to_return = True
        for element in root.iter("{%s}string" % c):
            if strings == '':
                strings = '- ' + element.text
            else:
                strings += '\n\n- ' + element.text
        if strings == '':
            for element in root.iter("{%s}Body" % s):
                strings = etree.tostring(element, pretty_print=True)
            if strings == '':
                strings = etree.tostring(root, pretty_print=True)
        self.write({
            'get_status_event_status_code': status_code,
            'get_status_event_response': strings,
            'response_eve_dian' : strings})
        self.create_records_from_xml()
        return True

    @api.model
    def _get_time(self):
        fmt = "%H:%M:%S"
        now_utc = datetime.now(timezone("UTC"))
        now_time = now_utc.strftime(fmt)
        return now_time

    @api.model
    def _get_time_colombia(self):
        fmt = "%H:%M:%S-05:00"
        now_utc = datetime.datetime.now(timezone("UTC"))
        now_time = now_utc.strftime(fmt)
        return now_time


        
    def generar_invoice_tax(self, invoice):
        """
        Método principal, conserva tu estructura original:
        - Determina si es documento de soporte (compra)
        - Procesa líneas, retenciones, redondeos finales, cufe, etc.
        - Usa la función 'update_tax_values' para cada impuesto/retención
        e incluye la lógica especial de reteIVA (05) y reteICA (07).
        """
        invoice.fecha_xml = fields.Datetime.to_string(datetime.datetime.now(tz=timezone('America/Bogota')))
        invoice.fecha_entrega = invoice.fecha_entrega or fields.Datetime.to_string(datetime.datetime.now(tz=timezone('America/Bogota')))

        calculation_rate = invoice.current_exchange_rate if invoice.currency_id.name != 'COP' else 1

        tax_total_values = {}
        ret_total_values = {}
        invoice_lines = []
        tax_exclusive_amount = 0
        tax_exclusive_amount_discount = 0
        total_impuestos = 0
        invoice.total_withholding_amount = 0.0

        rete_cop = {'rete_fue_cop': 0.0, 'rete_iva_cop': 0.0, 'rete_ica_cop': 0.0}
        tax_cop = {'tot_iva_cop': 0.0, 'tot_inc_cop': 0.0, 'tot_bol_cop': 0.0, 'imp_otro_cop': 0.0}

        # Bandera doc. soporte (compra)
        is_support_doc = invoice.move_type in ('in_invoice','in_refund')

        # -------------------------------------------------------------------------
        # 1) Procesamos líneas (impuestos y retenciones)
        # -------------------------------------------------------------------------

        def update_tax_values(tax_dict, tax, line, invoice, calculation_rate):
            """
            Función auxiliar para actualizar los valores de impuestos o retenciones en 'tax_dict'.
            - Redondea montos a 2 decimales.
            - Aplica lógica especial para:
                reteIVA (tax.tributes == '05')
                reteICA (tax.tributes == '07')
            Ajustando base y tasa (per_unit_amount) según corresponda.
            """
            # 1) Calculamos price_unit y taxes_result con redondeo
            price_unit = round(line.price_unit * (1 - (line.discount or 0.0) / 100.0), 2)
            taxes_result = tax.compute_all(price_unit, line.currency_id, line.quantity, line.product_id, invoice.partner_id)

            # 2) Redondeamos los montos a 2 decimales
            total_excluded = round(taxes_result['total_excluded'] * calculation_rate, 2)
            computed_amount = round(sum(t['amount'] for t in taxes_result['taxes']) * calculation_rate, 2)

            # 3) Creamos la estructura si no existe el código 'tax.codigo_dian'
            if tax.codigo_dian not in tax_dict:
                tax_dict[tax.codigo_dian] = {'total': 0, 'info': {}}

            # 4) Usamos la tasa del impuesto como 'key' en 'info'
            tax_rate_key = round(tax.amount, 2)
            if tax_rate_key not in tax_dict[tax.codigo_dian]['info']:
                tax_dict[tax.codigo_dian]['info'][tax_rate_key] = {
                    'taxable_amount': total_excluded,
                    'value': computed_amount,
                    'technical_name': tax.nombre_dian,
                    'amount_type': tax.amount_type,
                    'per_unit_amount': tax_rate_key,
                }
            else:
                info = tax_dict[tax.codigo_dian]['info'][tax_rate_key]
                info['taxable_amount'] = round(info['taxable_amount'] + total_excluded, 2)
                info['value'] = round(info['value'] + computed_amount, 2)

            # Acumulamos en 'total'
            tax_dict[tax.codigo_dian]['total'] = round(tax_dict[tax.codigo_dian]['total'] + computed_amount, 2)

            # -------------------------------------------------------------------------
            # LÓGICA ESPECIAL: reteIVA (tributes=='05')
            # -------------------------------------------------------------------------
            if tax.tributes == '05':
                # Ejemplo: base es el monto de '01' y la tasa se fuerza a 15
                line_all = line.tax_ids.compute_all(price_unit, line.currency_id, line.quantity, line.product_id, invoice.partner_id)
                base_for_reteiva = 0.0
                for t_item in line_all['taxes']:
                    matched_tax = line.tax_ids.filtered(lambda x: x.id == t_item['id'])
                    if matched_tax and matched_tax.tributes == '01':
                        base_for_reteiva += t_item['amount']
                base_for_reteiva_c = round(base_for_reteiva * calculation_rate, 2)

                tax_dict[tax.codigo_dian]['info'][tax_rate_key]['per_unit_amount'] = 15
                tax_dict[tax.codigo_dian]['info'][tax_rate_key]['taxable_amount'] = base_for_reteiva_c
        for line in invoice.invoice_line_ids.filtered(lambda l: l.display_type == 'product' and not l.product_id.enable_charges):
            price_unit = round(line.price_unit * (1 - (line.discount or 0.0) / 100.0), 2)
            taxes_res = line.tax_ids.compute_all(price_unit, line.currency_id, line.quantity, line.product_id, invoice.partner_id)

            # Redondear y sumar a tax_exclusive_amount
            tax_excl = round(taxes_res['total_excluded'] * calculation_rate, 2)
            tax_exclusive_amount = round(tax_exclusive_amount + tax_excl, 2)

            # total_impuestos => suma de impuestos > 0
            sum_positive = round(sum(t['amount'] for t in taxes_res['taxes'] if t['amount'] > 0) * calculation_rate, 2)
            total_impuestos = round(total_impuestos + sum_positive, 2)

            for tax in line.tax_ids:
                # Si es doc. soporte => solo tributos '01','06','07'
                if is_support_doc and tax.tributes not in ('01','06','07'):
                    continue

                if tax.tributes == 'ZZ':
                    continue

                # Impuesto >= 0 => tax_total_values; < 0 => ret_total_values
                if tax.amount >= 0:
                    update_tax_values(tax_total_values, tax, line, invoice, calculation_rate)
                else:
                    update_tax_values(ret_total_values, tax, line, invoice, calculation_rate)

        invoice.total_withholding_amount = round(sum(abs(ret['total']) for ret in ret_total_values.values()), 2)

        # -------------------------------------------------------------------------
        # 2) Recorremos líneas nuevamente para armar invoice_lines
        # -------------------------------------------------------------------------
        for index, line in enumerate(invoice.invoice_line_ids.filtered(lambda l: l.display_type == 'product' and l.price_unit >= 0)):
            price_unit = round(line.price_unit * (1 - (line.discount or 0.0) / 100.0), 2)
            taxes = line.tax_ids.compute_all(price_unit, line.currency_id, line.quantity, line.product_id, invoice.partner_id)

            tax_info = {}
            ret_info = {}
            for tax in line.tax_ids:
                if is_support_doc and tax.tributes not in ('01','06','07'):
                    continue

                if tax.tributes == 'ZZ':
                    continue

                if tax.amount >= 0:
                    update_tax_values(tax_info, tax, line, invoice, calculation_rate)
                else:
                    update_tax_values(ret_info, tax, line, invoice, calculation_rate)

            discount_line = round(line.price_unit * line.quantity * line.discount / 100 * calculation_rate, 2) if line.discount else 0
            discount_percentage = round(line.discount or 0, 2)
            base_discount = round(line.price_unit * line.quantity * calculation_rate, 2) if line.discount else 0

            tax_exclusive_amount_discount = round(tax_exclusive_amount_discount + discount_line, 2)

            if not line.product_id.enable_charges:
                code = invoice._get_product_code(line)
                mapa_line = invoice._prepare_invoice_line_data(
                    line, index, tax_info, ret_info, 
                    discount_line, discount_percentage, base_discount,
                    code, taxes, calculation_rate
                )
                invoice_lines.append(mapa_line)

        # -------------------------------------------------------------------------
        # 3) Ajustes de redondeo final, cufe, etc.
        # -------------------------------------------------------------------------
        amount_untaxed_signed = abs(invoice.amount_untaxed_signed)
        tax_exclusive_amount_decimal = round(tax_exclusive_amount, 2)
        rounding_difference = 0.0
        rounding_discount = 0.0
        rounding_charge = 0.0
        rounding_lines = invoice.line_ids.filtered(lambda line:
            line.display_type == 'rounding'
            or (line.product_id.default_code == 'RED' and line.product_id.enable_charges)
        )
        total_rounding = round(sum(rounding_lines.mapped('balance')), 2)
        if invoice.move_type == 'out_refund':
            total_rounding = round(total_rounding * -1, 2)
        if total_rounding < 0:
            rounding_charge = float(total_rounding)
        else:
            rounding_discount = float(abs(total_rounding))
            tax_exclusive_amount_decimal = round(tax_exclusive_amount_decimal + rounding_discount, 2)
        is_charge = total_rounding < 0
        adjustment_amount = abs(total_rounding)
        rounding_adjustment_data = None
        if adjustment_amount != 0:
            multiplier = 0.0
            if tax_exclusive_amount_decimal != 0:
                multiplier = round(((adjustment_amount / tax_exclusive_amount_decimal) * 100), 6)
            rounding_adjustment_data = {
                'ID': '3' if is_charge else '2',
                'ChargeIndicator': 'true' if is_charge else 'false',
                'AllowanceChargeReason': 'Cargo por ajuste al peso' if is_charge else 'Descuento por ajuste al peso',
                'MultiplierFactorNumeric': '{:.6f}'.format(multiplier),
                'Amount': '{:.2f}'.format(abs(adjustment_amount)),
                'BaseAmount': '{:.2f}'.format(abs(tax_exclusive_amount_decimal)),
                'CurrencyID': invoice.currency_id.name
            }

        total_impuestos = round(total_impuestos, 2)
        cufe_cuds, qr, cude_seed, qr_code = invoice.calcular_cufe_cuds(
            tax_total_values, abs(tax_exclusive_amount),
            rounding_charge, rounding_discount,
            total_impuestos
        )
        if invoice.currency_id.name != 'COP':
            rete_cop, tax_cop = invoice.calculate_cop_taxes(tax_total_values, ret_total_values, calculation_rate)
        else:
            rete_cop = {'rete_fue_cop': 0, 'rete_iva_cop': 0, 'rete_ica_cop': 0}
            tax_cop = {'tot_iva_cop': 0, 'tot_inc_cop': 0, 'tot_bol_cop': 0, 'imp_otro_cop': 0}

        # -------------------------------------------------------------------------
        # 4) Se retorna el diccionario final con la misma estructura de siempre
        # -------------------------------------------------------------------------
        return {
            'cufe': cufe_cuds,
            'cude_seed': cude_seed,
            'qr': qr,
            'qr_code': qr_code,
            'rounding_discount': '{:.2f}'.format(abs(rounding_discount)),
            'rounding_charge': '{:.2f}'.format(abs(rounding_charge)),
            'ret_total_values': ret_total_values,
            'tax_total_values': tax_total_values,
            'invoice_lines': invoice_lines,
            'currency_id': 'COP',
            'current_exchange_rate': calculation_rate,
            'invoice_note': invoice.remove_accents(html2plaintext(invoice.narration)) if not is_html_empty(invoice.narration) else '',
            'invoice_customer_commercial_registration': invoice.get_customer_commercial_registration(),
            'ContactName': invoice.partner_contact_id.name,
            'ContactTelephone': invoice.partner_contact_id.phone or '',
            'ContactElectronicMail': invoice.partner_contact_id.email or '',
            'line_extension_amount': '{:.2f}'.format(tax_exclusive_amount),
            'tax_inclusive_amount': '{:.2f}'.format(tax_exclusive_amount + total_impuestos),
            'tax_exclusive_amount': '{:.2f}'.format(tax_exclusive_amount),
            'payable_amount': '{:.2f}'.format(abs(abs(tax_exclusive_amount + total_impuestos) + abs(0))),
            'rete_fue_cop': rete_cop['rete_fue_cop'],
            'rete_iva_cop': rete_cop['rete_iva_cop'],
            'rete_ica_cop': rete_cop['rete_ica_cop'],
            'tot_iva_cop': tax_cop['tot_iva_cop'],
            'tot_inc_cop': tax_cop['tot_inc_cop'],
            'tot_bol_cop': tax_cop['tot_bol_cop'],
            'imp_otro_cop': tax_cop['imp_otro_cop'],
            'rounding_adjustment_data': rounding_adjustment_data,
            'fixed_taxes': {},  # Campo para futura implementación de impuestos fijos
        }
    def _get_product_code(self, line):
        if line.move_id.fe_type == '02':
            if not line.product_id.dian_customs_code:
                raise UserError(_('Las facturas de exportación requieren un código aduanero en todos los productos, completa esta información antes de validar la factura.'))
            return [line.product_id.dian_customs_code, '020', '195', 'Partida Arancelarias']
        if line.product_id.barcode:
            return [line.product_id.barcode, '010', '9', 'GTIN']
        elif line.product_id.unspsc_code_id:
            return [line.product_id.unspsc_code_id.code, '001', '10', 'UNSPSC']
        elif line.product_id.default_code:
            return [line.product_id.default_code, '999', '', 'Estándar de adopción del contribuyente']
        return ['NA', '999', '', 'Estándar de adopción del contribuyente']

    def calculate_cop_taxes(self, tax_total_values, ret_total_values, calculation_rate):
        rete_cop = {'rete_fue_cop': 0.0, 'rete_iva_cop': 0.0, 'rete_ica_cop': 0.0}
        tax_cop = {'tot_iva_cop': 0.0, 'tot_inc_cop': 0.0, 'tot_bol_cop': 0.0, 'imp_otro_cop': 0.0}
        
        for tax_type, ret_total in ret_total_values.items():
            if tax_type == '05':
                rete_cop['rete_iva_cop'] = abs(ret_total['total']) * calculation_rate
            elif tax_type == '06':
                rete_cop['rete_fue_cop'] = abs(ret_total['total']) * calculation_rate
            elif tax_type == '07':
                rete_cop['rete_ica_cop'] = abs(ret_total['total']) * calculation_rate
        
        for tax_type, tax_total in tax_total_values.items():
            if tax_type == '01':
                tax_cop['tot_iva_cop'] = tax_total['total'] * calculation_rate
            elif tax_type == '04':
                tax_cop['tot_inc_cop'] = tax_total['total'] * calculation_rate
            elif tax_type == '22':
                tax_cop['tot_bol_cop'] = tax_total['total'] * calculation_rate
            else:
                tax_cop['imp_otro_cop'] += tax_total['total'] * calculation_rate
        
        return rete_cop, tax_cop
    def _prepare_invoice_line_data(self, line, index, tax_info, ret_info, discount_line, discount_percentage, base_discount, code, taxes, calculation_rate):
        return {
            'id': index + 1,
            'product_id': line.product_id,
            'invoiced_quantity': line.quantity,
            'uom_product_id': line.product_uom_id,
            'line_extension_amount': taxes['total_excluded'] * calculation_rate,
            'item_description': saxutils.escape(line.name),
            'price': (taxes['total_excluded'] / line.quantity) * calculation_rate,
            'total_amount_tax': sum(tax['amount'] for tax in taxes['taxes'] if tax['amount'] > 0) * calculation_rate,
            'tax_info': tax_info,
            'ret_info': ret_info,
            'discount': discount_line,
            'discount_percentage': discount_percentage,
            'base_discount': base_discount,
            'invoice_start_date': datetime.datetime.now().astimezone(pytz.timezone("America/Bogota")).strftime('%Y-%m-%d'),
            'transmission_type_code': 1,
            'transmission_description': 'Por operación',
            'discount_text':  dict(line._fields['invoice_discount_text'].selection).get(line.invoice_discount_text),
            'discount_code': line.invoice_discount_text,
            'multiplier_discount': discount_percentage,
            'line_trade_sample_price': line.line_trade_sample_price * calculation_rate,
            'line_price_reference': (line.line_price_reference * line.quantity) * calculation_rate,
            'brand_name': line.product_id.brand_id.name,
            'model_name': line.product_id.model_id.name,
            'StandardItemIdentificationID': code[0],
            'StandardItemIdentificationschemeID': code[1],
            'StandardItemIdentificationschemeAgencyID': code[2],
            'StandardItemIdentificationschemeName': code[3]
        }
    def _get_product_code(self, line):
        if line.move_id.fe_type == '02':
            if not line.product_id.dian_customs_code:
                raise UserError(_('Las facturas de exportación requieren un código aduanero en todos los productos, completa esta información antes de validar la factura.'))
            return [line.product_id.dian_customs_code, '020', '195', 'Partida Arancelarias']
        if line.product_id.barcode:
            return [line.product_id.barcode, '010', '9', 'GTIN']
        elif line.product_id.unspsc_code_id:
            return [line.product_id.unspsc_code_id.code, '001', '10', 'UNSPSC']
        elif line.product_id.default_code:
            return [line.product_id.default_code, '999', '', 'Estándar de adopción del contribuyente']
        return ['NA', '999', '', 'Estándar de adopción del contribuyente']



    def get_customer_commercial_registration(self):
        if self.partner_id and self.partner_id.business_name:
            return self.partner_id.business_name
        elif not self.partner_id and self.partner_id.parent_id.business_name:
            return self.partner_id.parent_id.business_name
        else:
            return 0

    def calculate_cop_taxes(self, tax_total_values, ret_total_values, calculation_rate):
        rete_cop = {'rete_fue_cop': 0.0, 'rete_iva_cop': 0.0, 'rete_ica_cop': 0.0}
        tax_cop = {'tot_iva_cop': 0.0, 'tot_inc_cop': 0.0, 'tot_bol_cop': 0.0, 'imp_otro_cop': 0.0}
        
        for tax_type, ret_total in ret_total_values.items():
            if tax_type == '05':
                rete_cop['rete_iva_cop'] = abs(ret_total['total']) * calculation_rate
            elif tax_type == '06':
                rete_cop['rete_fue_cop'] = abs(ret_total['total']) * calculation_rate
            elif tax_type == '07':
                rete_cop['rete_ica_cop'] = abs(ret_total['total']) * calculation_rate
        
        for tax_type, tax_total in tax_total_values.items():
            if tax_type == '01':
                tax_cop['tot_iva_cop'] = tax_total['total'] * calculation_rate
            elif tax_type == '04':
                tax_cop['tot_inc_cop'] = tax_total['total'] * calculation_rate
            elif tax_type == '22':
                tax_cop['tot_bol_cop'] = tax_total['total'] * calculation_rate
            else:
                tax_cop['imp_otro_cop'] += tax_total['total'] * calculation_rate
        
        return rete_cop, tax_cop

    def calcular_cufe_cuds(self, tax_total_values, amount_untaxed, rounding_charge, rounding_discount,total_impuestos):
        if self.move_type in ["out_invoice", "out_refund"]:
            return self.calcular_cufe(tax_total_values, amount_untaxed, rounding_charge, rounding_discount,total_impuestos)
        elif self.move_type in ["in_invoice", "in_refund"]:
            return self.calcular_cuds(tax_total_values, amount_untaxed, rounding_charge, rounding_discount,total_impuestos)

    def _generate_qr_code(self, silent_errors=False):
        self.ensure_one()
        if self.company_id.country_code == 'CO':
            payment_url = self.diancode_id.qr_data or self.cufe_seed or self.name
            barcode = self.env['ir.actions.report'].barcode(barcode_type="QR", value=payment_url, width=120, height=120)
            return image_data_uri(base64.b64encode(barcode))
        return super()._generate_qr_code(silent_errors)


    def calcular_cufe(self, tax_total_values,amount_untaxed, rounding_charge, rounding_discount,total_impuestos):
        rec_active_resolution = (self.journal_id.sequence_id.dian_resolution_ids.filtered(lambda r: r.active_resolution))
        tax_computed_values = {tax: value['total'] for tax, value in tax_total_values.items()}

        numfac = self.name
        fecfac = self.fecha_xml.date().isoformat()
        horfac = self.fecha_xml.strftime("%H:%M:%S-05:00")
        valfac = '{:.2f}'.format(abs(amount_untaxed))
        codimp1 = '01'
        valimp1 = '{:.2f}'.format(tax_computed_values.get('01', 0))
        codimp2 = '04'
        valimp2 = '{:.2f}'.format(tax_computed_values.get('04', 0))
        codimp3 = '03'
        valimp3 = '{:.2f}'.format(tax_computed_values.get('03', 0))
        valtot = '{:.2f}'.format(abs(amount_untaxed) + abs(total_impuestos) + abs(rounding_charge) - abs(rounding_discount))
        contacto_compañia = self.company_id.partner_id
        nitofe = str(contacto_compañia.vat_co)
        if self.company_id.production:
            tipoambiente = '1'
        else:
            tipoambiente = '2'
        numadq = str(self.partner_id.vat_co) or str(self.partner_id.parent_id.vat_co)
        if self.move_type == 'out_invoice' and not self.is_debit_note:
            citec =  rec_active_resolution.technical_key
        else:
            citec = self.company_id.software_pin

        total_otros_impuestos = sum([value for key, value in tax_computed_values.items() if key != '01'])
        iva = tax_computed_values.get('01', '0.00')
                #1
        cufe = unidecode(
            str(numfac) + str(fecfac) + str(horfac) + str(valfac) + str(codimp1) + str(valimp1) + str(codimp2) +
            str(valimp2) + str(codimp3) + str(valimp3) + str(valtot) + str(nitofe) + str(numadq) + str(citec) +
            str(tipoambiente))
        cufe_seed = cufe

        sha384 = hashlib.sha384()
        sha384.update(cufe.encode())
        cufe = sha384.hexdigest()

        qr_code = 'NumFac: {}\n' \
                  'FecFac: {}\n' \
                  'HorFac: {}\n' \
                  'NitFac: {}\n' \
                  'DocAdq: {}\n' \
                  'ValFac: {}\n' \
                  'ValIva: {}\n' \
                  'ValOtroIm: {:.2f}\n' \
                  'ValFacIm: {}\n' \
                  'CUFE: {}'.format(
                    numfac,
                    fecfac,
                    horfac,
                    nitofe,
                    numadq,
                    valfac,
                    iva,
                    total_otros_impuestos,
                    valtot,
                    cufe
                    )

        qr = pyqrcode.create(qr_code, error='L')        
        return cufe, qr.png_as_base64_str(scale=2),cufe_seed,qr_code

    def calcular_cuds(self, tax_total_values, amount_untaxed, rounding_charge, rounding_discount,total_impuestos):
        tax_computed_values = {tax: value['total'] for tax, value in tax_total_values.items()}
        numfac = self.name
        fecfac = self.fecha_xml.date().isoformat()
        horfac = self.fecha_xml.strftime("%H:%M:%S-05:00")
        valfac = '{:.2f}'.format(abs(amount_untaxed))
        codimp1 = '01'
        valimp1 = '{:.2f}'.format(tax_computed_values.get('01', 0))
        valtot = '{:.2f}'.format(abs(amount_untaxed) + abs(total_impuestos) + abs(rounding_charge) - abs(rounding_discount))
        company_contact = self.company_id.partner_id
        nitofe = str(company_contact.vat_co)
        if self.company_id.production:
            tipoambiente = '1'
        else:
            tipoambiente = '2'
        numadq = str(self.partner_id.vat_co) or str(self.partner_id.parent_id.vat_co)
        citec = self.company_id.software_pin

        total_otros_impuestos = sum([value for key, value in tax_computed_values.items() if key != '01'])
        iva = tax_computed_values.get('01', '0.00')

        cuds =  unidecode(
            str(numfac) + str(fecfac) + str(horfac) + str(valfac) + str(codimp1) + str(valimp1) + str(valtot) +
            str(numadq) + str(nitofe) + str(citec) + str(tipoambiente)
        )
        cuds_seed = cuds

        sha384 = hashlib.sha384()
        sha384.update(cuds.encode())
        cuds = sha384.hexdigest()

        if not self.company_id.production:
            qr_code = 'NumFac: {}\n' \
                    'FecFac: {}\n' \
                    'HorFac: {}\n' \
                    'NitFac: {}\n' \
                    'DocAdq: {}\n' \
                    'ValFac: {}\n' \
                    'ValIva: {}\n' \
                    'ValOtroIm: {:.2f}\n' \
                    'ValFacIm: {}\n' \
                    'CUDS: {}\n' \
                    'https://catalogo-vpfe-hab.dian.gov.co/document/searchqr?documentkey={}'.format(
                    numfac,
                    fecfac,
                    horfac,
                    nitofe,
                    numadq,
                    valfac,
                    iva,
                    total_otros_impuestos,
                    valtot,
                    cuds,
                    cuds
                    )
        else:
            qr_code = 'NumFac: {}\n' \
                  'FecFac: {}\n' \
                  'HorFac: {}\n' \
                  'NitFac: {}\n' \
                  'DocAdq: {}\n' \
                  'ValFac: {}\n' \
                  'ValIva: {}\n' \
                  'ValOtroIm: {:.2f}\n' \
                  'ValFacIm: {}\n' \
                  'CUDS: {}\n' \
                  'https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={}'.format(
                    numfac,
                    fecfac,
                    horfac,
                    nitofe,
                    numadq,
                    valfac,
                    iva,
                    total_otros_impuestos,
                    valtot,
                    cuds,
                    cuds
                    )

        qr = pyqrcode.create(qr_code, error='L')

        return cuds, qr.png_as_base64_str(scale=2),cuds_seed,qr_code



    def remove_accents(self, chain):
        s = ''.join((c for c in unicodedata.normalize('NFD', chain) if unicodedata.category(c) != 'Mn'))
        return s

class InvoiceLine(models.Model):
    _inherit = "account.move.line"
    line_price_reference = fields.Float(string='Precio de referencia')
    line_trade_sample_price = fields.Selection(string='Tipo precio de referencia',
                                               related='move_id.trade_sample_price')
    line_trade_sample = fields.Boolean(string='Muestra comercial', related='move_id.invoice_trade_sample')
    invoice_discount_text = fields.Selection(
        selection=[
            ('00', 'Descuento no condicionado'),
            ('01', 'Descuento condicionado')
        ],
        string='Motivo de Descuento',
    )

    def _l10n_co_dian_net_price_subtotal(self):
        """ Returns the price subtotal after discount in company currency. """
        self.ensure_one()
        return self.move_id.direction_sign * self.balance

    def _l10n_co_dian_gross_price_subtotal(self):
        """ Returns the price subtotal without discount in company currency. """
        self.ensure_one()
        if self.discount == 100.0:
            return 0.0
        else:
            net_price_subtotal = self._l10n_co_dian_net_price_subtotal()
            return self.company_id.currency_id.round(net_price_subtotal / (1.0 - (self.discount or 0.0) / 100.0))

class receiptCode(models.Model):
    _name = 'receipt.code'
    _description = 'Receipt'

    name = fields.Char('Name')
    move_id = fields.Many2one("account.move")