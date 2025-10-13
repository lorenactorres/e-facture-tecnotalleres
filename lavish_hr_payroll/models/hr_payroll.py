# -*- coding: utf-8 -*-
from odoo import models, fields, api, _, SUPERUSER_ID , tools
from odoo.exceptions import UserError, ValidationError
from .browsable_object import BrowsableObject, InputLine, WorkedDays, Payslips, LeavedDays, ResultRules
from .browsable_object import ResultRules_co
import math
from collections import defaultdict, Counter
import calendar
from datetime import datetime, timedelta, date, time
from calendar import monthrange
from odoo.tools import float_round, date_utils
from odoo.tools.misc import format_date
from odoo import registry as registry_get
import math
import pytz
import odoo
import threading
import time
from odoo.tools import float_round, date_utils, float_is_zero
from odoo.tools.float_utils import float_compare
from odoo.tools.misc import format_date
from dateutil.relativedelta import relativedelta
from collections import defaultdict
import json
import logging
from typing import List
import base64
from ast import literal_eval
from markupsafe import Markup as mark_safe
_logger = logging.getLogger(__name__)
DAY_TYPE = [
    ('W', 'Trabajado'),
    ('A', 'Ausencia'),
    ('X', 'Sin contrato'), 
]

#---------------------------LIQUIDACIÓN DE NÓMINA-------------------------------#
class HrPayslipRun(models.Model):
    _name = 'hr.absence.days'
    _description = 'Ausencias'

    sequence = fields.Integer(string='Secuencia',required=True, index=True, default=5,
                              help='Use to arrange calculation sequence')
    payroll_id = fields.Many2one('hr.payslip', string='Payroll')
    employee_id = fields.Many2one('hr.employee', string='Employee')
    leave_type = fields.Char(string='Leave Type')
    total_days = fields.Float(string='Dias Totales')
    days_used = fields.Float(string='Dias a usar')
    days = fields.Float(string='Dias Usado',compute="_days_used")
    total = fields.Float(string='pendiente', compute="_total_leave")
    leave_id = fields.Many2one('hr.leave', string='Novedad')
    work_entry_type_id = fields.Many2one('hr.work.entry.type', related="leave_id.holiday_status_id.work_entry_type_id", string='Type', required=True, help="The code that can be used in the salary rules")

    
    @api.depends('total_days','days_used')
    def _total_leave(self):
        for rec in self:
            rec.total = rec.days_used + rec.days

    @api.depends('total_days', 'days_used')
    def _days_used(self):
        self.days = 0.0

class HrPayslipRun(models.Model):
    _name = 'hr.payslip.run'
    _inherit = ['hr.payslip.run','mail.thread', 'mail.activity.mixin']
    @api.model
    def _get_default_structure(self):
        return self.env['hr.payroll.structure'].search([('process','=','nomina')],limit=1)

    time_process = fields.Char(string='Tiempo ejecución')
    observations = fields.Text('Observaciones')
    definitive_plan = fields.Boolean(string='Plano definitivo generado')
    hr_payslip_line_ids = fields.One2many('hr.payslip.line', 'run_id')
    move_line_ids = fields.One2many('account.move.line', 'run_id')
    date_liquidacion = fields.Date('Fecha liquidación de contrato')
    date_prima = fields.Date('Fecha liquidación de prima')
    date_cesantias = fields.Date('Fecha liquidación de cesantías')
    pay_cesantias_in_payroll = fields.Boolean('¿Liquidar Interese de cesantia en nómina?')
    pay_primas_in_payroll = fields.Boolean('¿Liquidar Primas en nómina?')
    structure_id = fields.Many2one('hr.payroll.structure', string='Tipo de nomina', default=_get_default_structure)
    struct_process = fields.Selection(related='structure_id.process', string='Proceso', store=True)
    method_schedule_pay  = fields.Selection([('bi-weekly', 'Quincenal'),
                                            ('monthly', 'Mensual'),
                                            ('other', 'Ambos')], 'Frecuencia de Pago', default='other')
    analytic_account_ids = fields.Many2many('account.analytic.account', string='Cuentas analíticas')
    branch_ids = fields.Many2many('lavish.res.branch', string='Sucursales')
    state_contract = fields.Selection([('open','En Proceso'),('finished','Finalizado Por Liquidar')], string='Estado Contrato', default='open')
    settle_payroll_concepts = fields.Boolean('Liquida conceptos de nómina', default=True)
    novelties_payroll_concepts = fields.Boolean('Liquida conceptos de novedades', default=True)
    prima_run_reverse_id = fields.Many2one('hr.payslip.run', string='Lote de prima a ajustar')
    account_move_count = fields.Integer(compute='_compute_account_move_count')
    number = fields.Char(string='Número', readonly=True, copy=False)
    email_state = fields.Selection([
        ('draft', 'Pendiente'),
        ('sending', 'En Proceso'),
        ('sent', 'Enviado'),
        ('failed', 'Con Errores')
    ], string='Estado de Envío', default='draft', tracking=True)
    email_count = fields.Integer(string='Total Emails', compute='_compute_email_stats')
    email_sent = fields.Integer(string='Enviados', compute='_compute_email_stats')
    email_failed = fields.Integer(string='Fallidos', compute='_compute_email_stats')
    failed_payslips = fields.Text(string='Nóminas Fallidas', compute='_compute_email_stats')
    sequence_prefix = fields.Char(compute='_compute_sequence_prefix', store=True)
    is_credit_note = fields.Boolean('Nota de Crédito', default=False)
    employee_count = fields.Integer(compute='_compute_employee_count', string='Número de Empleados')
    payslip_count = fields.Integer(compute='_compute_payslip_count', string='Número de Nóminas')
    contract_count = fields.Integer(compute='_compute_counts')
    leave_count = fields.Integer(compute='_compute_counts')
    confirmed_payslip_count = fields.Integer(compute='_compute_counts')
    draft_payslip_count = fields.Integer(compute='_compute_counts')
    paid_payslip_count = fields.Integer(compute='_compute_counts')
    vacation_count = fields.Html(
        'Alerta de Vacaciones', 
        compute='_compute_warning_messages',
        sanitize=False,
        help='Muestra conteo de vacaciones pendientes y contratos por liquidar'
    )
    
    contract_state_warning = fields.Html(
        'Alerta de Contratos',
        compute='_compute_warning_messages',
        sanitize=False,
        help='Alerta sobre el estado de los contratos seleccionados'
    )
    liquidate_contract = fields.Boolean(
        '¿Liquidar Contratos?',
        default=False,
        help='Generar liquidación para contratos terminados'
    )
    liquidate_vacations = fields.Boolean(
        '¿Liquidar Vacaciones en Nómina?',
        default=False,
        help='Incluir liquidación de vacaciones en la nómina'
    )
    liquidation_structure_id = fields.Many2one(
        'hr.payroll.structure',
        string='Estructura de Liquidación',
        domain="[('process', '=', 'contrato')]",
        help='Estructura a usar para la liquidación de contratos'
    )

    @api.onchange('liquidate_contract')
    def _onchange_liquidate_contract(self):
        if self.liquidate_contract and not self.liquidation_structure_id:
            default_structure = self.env['hr.payroll.structure'].search(
                [('process', '=', 'contrato')], limit=1)
            self.liquidation_structure_id = default_structure


    def _compute_warning_messages(self):
        for run in self:
            vacation_count = []
            
            contracts = self.env['hr.contract'].search([('state', 'in', ('finished','close','open'))])

            if contracts:
                employee_ids = contracts.mapped('employee_id').ids
                
                base_domain = [
                    ('employee_id', 'in', employee_ids),
                    ('state', '=', 'validate'),
                    '|',
                        '&', ('request_date_from', '<=', run.date_start), ('request_date_to', '>=', run.date_start),
                        '&', ('request_date_from', '<=', run.date_end), ('request_date_to', '>=', run.date_start)
                ]

                time_leaves = self.env['hr.leave'].search(base_domain + [
                    ('holiday_status_id.is_vacation', '=', True),
                    ('holiday_status_id.is_vacation_money', '=', False),
                ])

                money_leaves = self.env['hr.leave'].search(base_domain + [
                    ('holiday_status_id.is_vacation_money', '=', True),
                ])

                unpaid_time_leaves = time_leaves.filtered(
                    lambda l: any(
                        line.state == 'validated' and line.state != 'paid' and
                        (line.date >= run.date_start and line.date <= run.date_end)
                        for line in l.line_ids
                    )
                )

                unpaid_money_leaves = money_leaves.filtered(
                    lambda l: any(
                        line.state == 'validated' and line.state != 'paid' and
                        (line.date >= run.date_start and line.date <= run.date_end)
                        for line in l.line_ids
                    )
                )

                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                action_id = self.env.ref('hr_holidays.hr_leave_action_action_approve_department').id
                menu_id = self.env.ref('hr_holidays.menu_hr_holidays_root').id

                if unpaid_time_leaves:
                    leave_ids = ','.join(map(str, unpaid_time_leaves.ids))
                    time_url = f"{base_url}/web#action={action_id}&menu_id={menu_id}&view_type=list&model=hr.leave&domain=[('id', 'in', [{leave_ids}])]"
                    vacation_count.append(
                        f"⚠️ <a href='{time_url}' target='_blank'>{len(unpaid_time_leaves)} Vacaciones en tiempo pendientes</a>"
                    )

                if unpaid_money_leaves:
                    money_ids = ','.join(map(str, unpaid_money_leaves.ids))
                    money_url = f"{base_url}/web#action={action_id}&menu_id={menu_id}&view_type=list&model=hr.leave&domain=[('id', 'in', [{money_ids}])]"
                    vacation_count.append(
                        f"💰 <a href='{money_url}' target='_blank'>{len(unpaid_money_leaves)} Vacaciones en dinero pendientes</a>"
                    )

                contracts_without_liquidation = contracts.filtered(
                    lambda c: (c.state in ['finished', 'close'] and not c.retirement_date) or 
                            (c.state == 'open' and c.date_end and c.date_end <= run.date_end)
                ).filtered(
                    lambda c: not self.env['hr.payslip'].search([
                        ('employee_id', '=', c.employee_id.id),
                        ('struct_id.process', '=', 'liquidacion'),
                        ('state', 'in', ['done', 'paid']),
                        ('date_from', '<=', c.date_end or run.date_end),
                        ('date_to', '>=', c.date_end or run.date_end),
                    ])
                )

                if contracts_without_liquidation:
                    contract_action = self.env.ref('hr_contract.action_hr_contract').id
                    contract_menu = self.env.ref('hr_contract.hr_menu_contract').id
                    contract_ids = ','.join(map(str, contracts_without_liquidation.ids))
                    contract_url = f"{base_url}/web#action={contract_action}&menu_id={contract_menu}&view_type=list&model=hr.contract&domain=[('id', 'in', [{contract_ids}])]"
                    
                    vacation_count.append(
                        f"🔔 <a href='{contract_url}' target='_blank'>{len(contracts_without_liquidation)} Contratos pendientes por liquidar</a>"
                    )

            run.vacation_count = mark_safe("<br/>".join(vacation_count)) if vacation_count else False
            run.contract_state_warning = mark_safe(
                "⚠️ Se encontraron registros que requieren atención. "
                "Haga clic en los enlaces arriba para revisar."
            ) if vacation_count else False

    @api.depends('slip_ids', 'slip_ids.state', 'slip_ids.paid', 'slip_ids.leave_ids')
    def _compute_counts(self):
        for record in self:
            record.contract_count = len(record.slip_ids.mapped('contract_id'))
            leaves = record.slip_ids.mapped('leave_ids.leave_id').filtered(lambda l: l.state == 'validate')
            record.leave_count = len(leaves)
            record.confirmed_payslip_count = len(record.slip_ids.filtered(lambda x: x.state == 'done'))
            record.draft_payslip_count = len(record.slip_ids.filtered(lambda x: x.state in ('draft','verify')))
            record.paid_payslip_count = len(record.slip_ids.filtered(lambda x: x.state == ('paid')))

    def action_view_contracts(self):
        return {
            'name': 'Contratos',
            'view_mode': 'tree,form',
            'res_model': 'hr.contract',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.slip_ids.mapped('contract_id').ids)],
        }

    def action_view_leaves(self):
        return {
            'name': 'Incapacidades',
            'view_mode': 'tree,form',
            'res_model': 'hr.leave',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.slip_ids.mapped('leave_ids.leave_id').filtered(lambda l: l.state == 'validate').ids)],
        }

    def action_view_confirmed_payslips(self):
        return {
            'name': 'Nóminas Confirmadas',
            'view_mode': 'tree,form',
            'res_model': 'hr.payslip',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.slip_ids.filtered(lambda x: x.state == 'done').ids)],
        }

    def action_view_draft_payslips(self):
        return {
            'name': 'Nóminas en Borrador',
            'view_mode': 'tree,form',
            'res_model': 'hr.payslip',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.slip_ids.filtered(lambda x: x.state == 'draft').ids)],
        }

    def action_view_paid_payslips(self):
        return {
            'name': 'Nóminas Pagadas',
            'view_mode': 'tree,form',
            'res_model': 'hr.payslip',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.slip_ids.filtered(lambda x: x.state == ('paid')).ids)],
        }
    def _compute_employee_count(self):
        for record in self:
            record.employee_count = len(record.slip_ids.mapped('employee_id'))

    def _compute_payslip_count(self):
        for record in self:
            record.payslip_count = len(record.slip_ids)

    def action_view_employees(self):
        self.ensure_one()
        return {
            'name': 'Empleados',
            'view_mode': 'tree,form',
            'res_model': 'hr.employee',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.slip_ids.mapped('employee_id').ids)],
        }

    def action_view_payslips(self):
        self.ensure_one()
        return {
            'name': 'Nóminas',
            'view_mode': 'tree,form',
            'res_model': 'hr.payslip',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', self.slip_ids.ids)],
        }

    def action_view_details(self):
        return {
            'name': 'Detalles',
            'view_mode': 'form',
            'res_model': 'hr.payslip.run',
            'type': 'ir.actions.act_window',
            'res_id': self.id,
        }

    @api.depends('slip_ids.mail_state')
    def _compute_email_stats(self):
        for record in self:
            all_slips = record.slip_ids
            sent_slips = all_slips.filtered(lambda s: s.mail_state == 'sent')
            failed_slips = all_slips.filtered(lambda s: s.mail_state == 'failed')
            
            record.email_count = len(all_slips)
            record.email_sent = len(sent_slips)
            record.email_failed = len(failed_slips)
            
            if failed_slips:
                failed_details = []
                for slip in failed_slips:
                    failed_details.append(f"{slip.employee_id.name}: {slip.mail_error_msg or 'Error desconocido'}")
                record.failed_payslips = "\n".join(failed_details)
            else:
                record.failed_payslips = False
    def action_send_payslip_emails(self):
        self.ensure_one()
        if not self.slip_ids:
            raise UserError(_("No hay nóminas para enviar."))
        self.email_state = 'sending'
        try:
            template = self.env.ref('lavish_hr_payroll.email_template_payslip_smart')
            if not template:
                raise UserError(_("No se encontró la plantilla de correo."))

            batch_size = 50
            payslips = self.slip_ids.filtered(lambda s: s.mail_state != 'sent')
            total = len(payslips)
            
            _logger.info(f"Iniciando envío masivo de {total} comprobantes de nómina del lote {self.name}")
            
            for i in range(0, total, batch_size):
                batch = payslips[i:i + batch_size]
                self._process_payslip_batch(batch, template)
                self.env.cr.commit() 
                _logger.info(f"Procesado lote {i//batch_size + 1} de {(total-1)//batch_size + 1}")

        except Exception as e:
            self.email_state = 'failed'
            _logger.error(f"Error en el proceso de envío masivo: {str(e)}")
            raise UserError(_(f"Error en el proceso de envío: {str(e)}"))

        finally:
            # Actualizar estado final
            failed_count = len(self.slip_ids.filtered(lambda s: s.mail_state == 'failed'))
            if failed_count:
                self.email_state = 'failed'
            else:
                self.email_state = 'sent'
            
            # Crear mensaje en el chatter
            sent_count = len(self.slip_ids.filtered(lambda s: s.mail_state == 'sent'))
            self.message_post(
                body=f"""<b>Proceso de envío completado</b><br/>
                        - Total procesados: {total}<br/>
                        - Enviados exitosamente: {sent_count}<br/>
                        - Fallidos: {failed_count}<br/>
                        {f'<br/><b>⚠️ Algunos comprobantes no pudieron ser enviados.</b>' if failed_count else ''}""",
                message_type='notification'
            )

    def _process_payslip_batch(self, payslips, template):
        for payslip in payslips:
            try:
                if not (payslip.employee_id.work_email or payslip.employee_id.personal_email):
                    raise UserError(_(f"El empleado {payslip.employee_id.name} no tiene configurado un correo electrónico."))

                report = payslip.struct_id.report_id
                if not report:
                    raise UserError(_(f"La estructura de nómina {payslip.struct_id.name} no tiene un reporte configurado."))

                pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(report, payslip.id)
                
                attachment_name = f"Comprobante_nomina_{payslip.employee_id.work_contact_id.vat or 'SIN-ID'}_{payslip.number or 'SIN-NUM'}.pdf"
                attachment = self.env['ir.attachment'].create({
                    'name': attachment_name,
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_content),
                    'res_model': 'hr.payslip',
                    'res_id': payslip.id,
                })

                # Enviar correo
                with self.env.cr.savepoint():
                    template.with_context(
                        force_send=True,
                        payslip_run=self.id,
                        lang=payslip.employee_id.lang 
                    ).send_mail(
                        payslip.id,
                        force_send=True,
                        email_values={
                            'attachment_ids': [(4, attachment.id)],
                            'auto_delete': False
                        },
                        raise_exception=True
                    )

                    # Actualizar estado de la nómina
                    payslip.write({
                        'mail_state': 'sent',
                        'mail_sent_date': fields.Datetime.now(),
                        'mail_error_msg': False
                    })
                    
                    _logger.info(f"Correo enviado exitosamente para {payslip.employee_id.name}")

            except Exception as e:
                error_msg = str(e)
                _logger.error(f"Error enviando nómina {payslip.name} de {payslip.employee_id.name}: {error_msg}")
                payslip.write({
                    'mail_state': 'failed',
                    'mail_error_msg': error_msg
                })



    @api.depends('structure_id', 'structure_id.process', 'is_credit_note')
    def _compute_sequence_prefix(self):
        for run in self:
            if run.structure_id and run.structure_id.process:
                prefix_map = {
                    'nomina': 'NOM' if not run.is_credit_note else 'RNOM',
                    'vacaciones': 'VAC' if not run.is_credit_note else 'RVAC',
                    'prima': 'PRI' if not run.is_credit_note else 'RPRI',
                    'cesantias': 'CES' if not run.is_credit_note else 'RCES',
                    'contrato': 'LIQ' if not run.is_credit_note else 'RLIQ',
                    'intereses_cesantias': 'INT' if not run.is_credit_note else 'RINT',
                    'otro': 'OTR' if not run.is_credit_note else 'ROTR'
                }
                run.sequence_prefix = prefix_map.get(run.structure_id.process, 'OTR')
            else:
                run.sequence_prefix = 'OTR'

    def _get_next_number(self):
        """Obtener siguiente número según estructura de nómina."""
        last_record = self.search([
            ('company_id', '=', self.env.company.id),
            ('structure_id', '=', self.structure_id.id),
            ('sequence_prefix', '=', self.sequence_prefix)
        ], order='number desc', limit=1)

        if last_record and last_record.number:
            try:
                base_number = last_record.number.split('/')[-1]
                next_number = int(base_number) + 1
            except (ValueError, IndexError):
                next_number = 1
        else:
            next_number = 1

        year = str(fields.Date.today().year)
        return f"{self.sequence_prefix}/{year}/{str(next_number).zfill(3)}"

    def _get_period_name(self, date):
        """Obtener nombre del período."""
        MESES = {
            1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
            5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
            9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
        }
        return f"{MESES.get(date.month, '')} {date.year}"

    @api.onchange('structure_id', 'date_start', 'is_credit_note')
    def _onchange_structure_and_date(self):
        if self.structure_id and self.date_start:
            period_name = self._get_period_name(self.date_start)
            prefix = "Reversión de " if self.is_credit_note else ""
            self.name = f"{prefix}{self.structure_id.name} - {period_name}"
            self._compute_sequence_prefix()  # Recalcular el prefijo
            self.number = self._get_next_number()
            if self.structure_id.process == 'nomina':
                self.date_end = (self.date_start + relativedelta(months=1, day=1, days=-1))
            elif self.structure_id.process in ['prima', 'cesantias', 'intereses_cesantias']:
                self.date_end = self.date_start
            else:
                self.date_end = (self.date_start + relativedelta(months=1, day=1, days=-1))
    @api.model_create_multi
    def create(self, vals_list):
        PayrollStructure = self.env['hr.payroll.structure']
        
        for vals in vals_list:
            if vals.get('structure_id'):
                # Create a temporary record for computations
                temp_record = self.new(vals)
                temp_record._compute_sequence_prefix()

                # Set number if not provided
                if not vals.get('number'):
                    vals['number'] = temp_record._get_next_number()
                
                # Set name if date_start exists and name not provided
                if vals.get('date_start') and not vals.get('name'):
                    structure = PayrollStructure.browse(vals['structure_id'])
                    date_start = fields.Date.from_string(vals['date_start'])
                    period_name = self._get_period_name(date_start)
                    
                    # Handle credit note prefix
                    prefix = "Reversión de " if vals.get('is_credit_note') else ""
                    vals['name'] = f"{prefix}{structure.name} - {period_name}"

        return super().create(vals_list)

    def action_reverse_payslip_run(self):
        """Crear una reversión del lote de nómina."""
        self.ensure_one()
        if self.state != 'close':
            raise UserError(_("Solo se pueden revertir lotes cerrados."))

        reversed_run = self.copy({
            'is_credit_note': True,
            'date_start': fields.Date.today(),
            'date_end': fields.Date.today(),
            'number': False, 
            'name': False, 
        })
        
        for slip in self.slip_ids:
            slip.copy({
                'payslip_run_id': reversed_run.id,
                'credit_note': True,
                'name': False,
            })

        action = {
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'hr.payslip.run',
            'res_id': reversed_run.id,
        }
        return action

    def compute_sheet(self):
        for rec in self:
            for line in rec.slip_ids:
                if line.state in ('draft', 'verify'):
                    line.compute_sheet()

    def compute_sheet_2(self):
        for rec in self:
            for line in rec.slip_ids:
                if line.state in ('draft', 'verify'):
                    line.compute_sheet_2()
    
    def action_payslip_done_2(self):
        for rec in self:
            for line in rec.slip_ids:
                if line.state in ('draft', 'verify','done'):
                    line.action_payslip_done_2()


    def assign_status_verify(self):
        for record in self:
            if len(record.slip_ids) > 0:
                record.write({'state':'verify'})
            else:
                raise ValidationError(_("No existen nóminas asociadas a este lote, no es posible pasar a estado verificar."))

    def action_validate(self):
        settings_batch_account = self.env['ir.config_parameter'].sudo().get_param('lavish_hr_payroll.module_hr_payroll_batch_account') or False
        slips_original = self.mapped('slip_ids').filtered(lambda slip: slip.state != 'cancel')
        if settings_batch_account == '1': 
            slips = slips_original.filtered(lambda x: len(x.move_id) == 0 or x.move_id == False)[0:200]
        else:
            slips = slips_original
        slips.action_payslip_done()
        if len(slips_original.filtered(lambda x: len(x.move_id) == 0 or x.move_id == False)) == 0:
            self.action_close()

    def restart_payroll_batch(self):
        self.mapped('slip_ids').action_payslip_cancel()
        self.mapped('slip_ids').unlink()
        return self.write({'state': 'draft','observations':False,'time_process':False})

    def restart_payroll_account_batch(self):
        for payslip in self.slip_ids:
            payslip.mapped('move_id').unlink()
            self.env['hr.vacation'].search([('payslip', '=', payslip.id)]).unlink()
            self.env['hr.history.prima'].search([('payslip', '=', payslip.id)]).unlink()
            self.env['hr.history.cesantias'].search([('payslip', '=', payslip.id)]).unlink()
            payslip.write({'state': 'verify'})
        return self.write({'state': 'verify'})

    def restart_full_payroll_batch(self):
        for payslip in self.slip_ids:
            payslip.mapped('move_id').unlink() 
            self.env['hr.vacation'].search([('payslip', '=', payslip.id)]).unlink()
            self.env['hr.history.prima'].search([('payslip', '=', payslip.id)]).unlink()
            self.env['hr.history.cesantias'].search([('payslip', '=', payslip.id)]).unlink()
            payslip.write({'state':'verify'})
            payslip.action_payslip_cancel()
            payslip.unlink()
        return self.write({'state': 'draft'})

    def _compute_account_move_count(self):
        for payslip_run in self:
            payslip_run.account_move_count = len(self.slip_ids.mapped('move_id'))

    def action_open_account_move(self):
        self.ensure_one()
        views = [(self.env.ref('account.view_move_tree').id, 'tree'),
                 (self.env.ref('account.view_move_form').id, 'form')]
        return {
            'name': _('Account Move'),
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'view_id': False,
            'views': views,
            'type': 'ir.actions.act_window',
            'domain': [['id', 'in', self.slip_ids.mapped('move_id').ids]],
        }


class HrPayslipEmployees(models.TransientModel):
    _inherit = 'hr.payslip.employees'	

    @api.model
    def _get_default_structure(self):
        return self.env['hr.payroll.structure'].search([('process','=','nomina')],limit=1)
    
    @api.model
    def _get_default_liquidate_contract(self):
        if self.env.context.get('active_model') == 'hr.payslip.run' and self.env.context.get('active_id'):
            payslip_run = self.env['hr.payslip.run'].browse(self.env.context['active_id'])
            return payslip_run.liquidate_contract
        return False

    @api.model
    def _get_default_pay_vacations(self):
        if self.env.context.get('active_model') == 'hr.payslip.run' and self.env.context.get('active_id'):
            payslip_run = self.env['hr.payslip.run'].browse(self.env.context['active_id'])
            return payslip_run.liquidate_vacations
        return False

    date_liquidacion = fields.Date('Fecha liquidación de contrato')
    date_prima = fields.Date('Fecha liquidación de prima')
    date_cesantias = fields.Date('Fecha liquidación de cesantías')
    pay_cesantias_in_payroll = fields.Boolean('¿Liquidar Interese de cesantia en nómina?')
    pay_primas_in_payroll = fields.Boolean('¿Liquidar Primas en nómina?')
    liquidate_contract = fields.Boolean(
        '¿Liquidar Contratos?',
        default=_get_default_liquidate_contract,
        help='Generar liquidación para contratos terminados'
    )
    structure_id = fields.Many2one('hr.payroll.structure', string='Salary Structure', default=_get_default_structure)
    struct_process = fields.Selection(related='structure_id.process', string='Proceso', store=True)
    method_schedule_pay  = fields.Selection([('bi-weekly', 'Quincenal'),
                                          ('monthly', 'Mensual'),
                                          ('other', 'Ambos')], 'Frecuencia de Pago', default='other')
    analytic_account_ids = fields.Many2many('account.analytic.account', string='Cuentas analíticas')
    branch_ids = fields.Many2many('lavish.res.branch', string='Sucursales')
    state_contract = fields.Selection([('open','En Proceso'),('finished','Finalizado Por Liquidar')], string='Estado Contrato', default='open')
    settle_payroll_concepts = fields.Boolean('Liquida conceptos de nómina', default=True)
    novelties_payroll_concepts = fields.Boolean('Liquida conceptos de novedades', default=True)
    prima_run_reverse_id = fields.Many2one('hr.payslip.run', string='Lote de prima a ajustar')
    pay_vacations_in_payroll = fields.Boolean(
        'Liquida Vacaciones de nómina',
        default=_get_default_pay_vacations,
        help='Activar para incluir liquidación de vacaciones en la nómina'
    )

    vacation_count = fields.Text(
        'Alerta de Vacaciones',
        compute='_compute_vacation_warnings',
        help='Conteo de vacaciones pendientes y contratos por liquidar'
    )

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        context = self.env.context
        
        if context.get('active_model') == 'hr.payslip.run' and context.get('active_ids'):
            payslip_run = self.env['hr.payslip.run'].browse(context.get('active_ids'))
            if payslip_run.liquidate_contract:
                res.update({
                    'liquidate_contract': True,
                })
            res['pay_vacations_in_payroll'] = payslip_run.liquidate_vacations
        
        return res

    def _compute_vacation_warnings(self):
        for wizard in self:
            warnings = []
            contracts = self.env['hr.contract'].search([('state', 'in', ('finished','close','open'))])
            if contracts.employee_id:
                date_start = fields.Date.to_date(self.env.context.get('default_date_start'))
                date_end = fields.Date.to_date(self.env.context.get('default_date_end'))
                employee_ids = contracts.employee_id.ids

                base_domain = [
                    ('employee_id', 'in', employee_ids),
                    ('state', '=', 'validate'),
                    '|',
                        '&', ('request_date_from', '<=', date_start), ('request_date_to', '>=', date_start),
                        '&', ('request_date_from', '<=', date_end), ('request_date_to', '>=', date_start)
                ]

                time_count = self.env['hr.leave'].search_count(
                    base_domain + [
                        ('holiday_status_id.is_vacation', '=', True),
                        ('holiday_status_id.is_vacation_money', '=', False),
                    ]
                )

                money_count = self.env['hr.leave'].search_count(
                    base_domain + [
                        ('holiday_status_id.is_vacation_money', '=', True),
                    ]
                )

                contract_count = self.env['hr.contract'].search_count([
                    ('employee_id', 'in', employee_ids),
                    ('state', 'in', ['finished', 'close']),
                    '|',
                        ('retirement_date', '=', False),
                        ('retirement_date', '=', None),
                    '|',
                        '&', ('date_start', '<=', date_end), ('date_end', '>=', date_start),
                        '&', ('date_start', '<=', date_end), ('date_end', '=', False)
                ])
                if time_count > 0:
                    warnings.append(f"⚠️ {time_count} Vacaciones en tiempo")
                if money_count > 0:
                    warnings.append(f"💰 {money_count} Vacaciones en dinero")
                if contract_count > 0:
                    warnings.append(f"🔔 {contract_count} Contratos por liquidar")
            wizard.vacation_count = " | ".join(warnings) if warnings else False

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'employee_ids' in fields_list and res.get('employee_ids'):
            self._compute_vacation_warnings()
        return res
    
    def _get_available_contracts_domain(self):
        domain = [('contract_id.state', '=', self.state_contract or 'open'), ('company_id', '=', self.env.company.id)]
        if self.method_schedule_pay and self.method_schedule_pay != 'other':
            domain.append(('contract_id.method_schedule_pay','=',self.method_schedule_pay))
        if len(self.analytic_account_ids) > 0:
            domain.append(('contract_id.employee_id.analytic_account_id', 'in', self.analytic_account_ids.ids))
        if len(self.branch_ids) > 0:
            domain.append(('branch_id', 'in', self.branch_ids.ids))
        if self.prima_run_reverse_id:
            employee_ids = self.env['hr.payslip'].search([('payslip_run_id', '=', self.prima_run_reverse_id.id)]).employee_id.ids
            domain.append(('id','in',employee_ids))
        if self.structure_id.process in ('cesantia','prima'):
            domain.append(('contract_id.modality_salary','!=','integral'))
            domain.append(('contract_id.employee_id.tipo_coti_id.code','not in', ['12', '19']))
        return domain

    @api.depends('structure_id','department_id','method_schedule_pay','analytic_account_ids','branch_ids','state_contract','prima_run_reverse_id')
    def _compute_employee_ids(self):
        for wizard in self:
            wizard._compute_vacation_warnings()
            domain = wizard._get_available_contracts_domain()
            if wizard.department_id:
                domain.append(('department_id', 'child_of', self.department_id.id))
            wizard.employee_ids = self.env['hr.employee'].search(domain)

    def _check_undefined_slots(self, work_entries, payslip_run):
        """
        Check if a time slot in the contract's calendar is not covered by a work entry
        """
        calendar_is_not_covered = self.env['hr.contract']
        work_entries_by_contract = defaultdict(lambda: self.env['hr.work.entry'])
        for work_entry in work_entries:
            work_entries_by_contract[work_entry.contract_id] |= work_entry

        for contract, work_entries in work_entries_by_contract.items():
            calendar_start = pytz.utc.localize(datetime.combine(max(contract.date_start, payslip_run.date_start), datetime.min.time()))
            calendar_end = pytz.utc.localize(datetime.combine(min(contract.date_end or date.max, payslip_run.date_end), datetime.max.time()))
            outside = contract.resource_calendar_id._attendance_intervals_batch(calendar_start, calendar_end)[False] - work_entries._to_intervals()
            if outside:
                calendar_is_not_covered |= contract
        return calendar_is_not_covered

    def _filter_contracts(self, contracts):
        return contracts

    def compute_sheet(self):
        self.ensure_one()
        if not self.env.context.get('active_id'):
            from_date = fields.Date.to_date(self.env.context.get('default_date_start'))
            end_date = fields.Date.to_date(self.env.context.get('default_date_end'))
            today = fields.date.today()
            first_day = today + relativedelta(day=1)
            last_day = today + relativedelta(day=31)
            if from_date == first_day and end_date == last_day:
                batch_name = from_date.strftime('%B %Y')
            else:
                batch_name = _('From %s to %s', format_date(self.env, from_date), format_date(self.env, end_date))
            payslip_run = self.env['hr.payslip.run'].create({
                'name': batch_name,
                'date_start': from_date,
                'date_end': end_date,
            })
        else:
            payslip_run = self.env['hr.payslip.run'].browse(self.env.context.get('active_id'))

        employees = self.with_context(active_test=False).employee_ids
        if not employees:
            raise UserError(_("You must select employee(s) to generate payslip(s)."))

        #Prevent a payslip_run from having multiple payslips for the same employee
        employees -= payslip_run.slip_ids.employee_id
        success_result = {
                'type': 'ir.actions.act_window',
                'res_model': 'hr.payslip.run',
                'views': [[False, 'form']],
                'res_id': payslip_run.id,
            }
        #try:
        payslips = self.env['hr.payslip']
        Payslip = self.env['hr.payslip']

        if self.structure_id.process == 'contrato':
            contracts = employees._get_contracts(payslip_run.date_start, payslip_run.date_end, states=['open', 'finished'])
        else:
            contracts = employees._get_contracts(payslip_run.date_start, payslip_run.date_end, states=['open']) 

        default_values = Payslip.default_get(Payslip.fields_get())
        payslips_vals = []
        for contract in self._filter_contracts(contracts):
            structure = self.structure_id
            if self.liquidate_contract and contract.state in ['finished', 'close']:
                structure = payslip_run.liquidation_structure_id
            values = dict(default_values, **{
                'name': _('New Payslip'),
                'employee_id': contract.employee_id.id,
                'payslip_run_id': payslip_run.id,
                'date_from': payslip_run.date_start,
                'date_to': payslip_run.date_end,
                'date_liquidacion': self.date_liquidacion,
                'date_prima': self.date_prima,
                'date_cesantias': self.date_cesantias,
                'pay_cesantias_in_payroll': self.pay_cesantias_in_payroll,
                'pay_vacations_in_payroll': self.pay_vacations_in_payroll,
                'pay_primas_in_payroll': self.pay_primas_in_payroll,
                'contract_id': contract.id,
                'struct_id': structure.id, #self.structure_id.id or contract.structure_type_id.default_struct_id.id,
            })
            payslips_vals.append(values)
        payslips = Payslip.with_context(tracking_disable=True).create(payslips_vals)
        payslips.compute_slip()
        payslip_run.state = 'verify'

        return success_result

    def clean_employees(self):   
        self.employee_ids = [(5,0,0)]
        return {
            'context': self.env.context,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'hr.payslip.employees',
            'res_id': self.id,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }
class hr_payslip_worked_days(models.Model):
    _inherit = 'hr.payslip.worked_days'
    symbol = fields.Char('+/-', size=8)
    number_of_days = fields.Float(digits='Payroll')
    number_of_hours = fields.Float(digits='Payroll')
    number_of_days_aux = fields.Float(digits='Payroll')
    number_of_hours_aux = fields.Float(digits='Payroll') 
    amount_aux = fields.Float(digits='Payroll', string="Auxilio Transporte")
    amount = fields.Monetary(string='Amount', compute=False, store=True, copy=True)

class Hr_payslip_line(models.Model):
    _inherit = 'hr.payslip.line'

    entity_id = fields.Many2one('hr.employee.entities', string="Entidad")
    loan_id = fields.Many2one('hr.loan', 'Prestamo', readonly=True)
    days_unpaid_absences = fields.Integer(string='Días de ausencias no pagadas',readonly=True)
    amount_base = fields.Float('Base')
    is_history_reverse = fields.Boolean(string='Es historico para reversar')
    run_id = fields.Many2one('hr.payslip.run', 'Lote de nomina')
    ex_rent = fields.Boolean('Aporte voluntario o ingreso exento de renta')
    leave_id = fields.Many2one(comodel_name='hr.leave', string='Ausencia')
    afc = fields.Boolean('AFC')
    computation = fields.Text('Log computación')
    #Campos informe detalle
    branch_employee_id = fields.Many2one(related='employee_id.branch_id', string='Sucursal', store=True)
    state_slip = fields.Selection(related='slip_id.state', string='Estado Nómina', store=True)
    analytic_account_slip_id = fields.Many2one(related='slip_id.analytic_account_id', string='Cuenta Analitica', store=True)
    struct_slip_id = fields.Many2one(related='slip_id.struct_id', string='Estructura', store=True)
    subtotal =  fields.Float('Subtotal')
    category_code = fields.Char(related='salary_rule_id.category_id.code', string='Categoría',readonly=True, store=True)
    concept_id = fields.Many2one('hr.contract.concepts', string='Concepto',  help='Concepto de nómina relacionado con esta línea')
    is_previous_period = fields.Boolean('Novedad Saltada')

    def count_category_ids(self):
        count_category_ids = self.env['hr.payslip.line'].search_count([('slip_id', '=', self.slip_id.id), ('category_id', '=', self.category_id.id)])
        return count_category_ids

    @api.depends('quantity', 'amount', 'rate','subtotal')
    def _compute_total(self):
        round_payroll = bool(self.env['ir.config_parameter'].sudo().get_param('lavish_hr_payroll.round_payroll')) or False
        for line in self:
            if line.subtotal != 0.0:
                line.total = line.subtotal
            else:
                amount_total_original = float(line.quantity) * line.amount * line.rate / 100
                line.total = amount_total_original

    def get_computation_data(self):
        """Recupera y convierte el JSON almacenado en computation a un diccionario"""
        if self.computation:
            return json.loads(self.computation)
        return {}

class Hr_payslip_not_line(models.Model):
    _name = 'hr.payslip.not.line'
    _description = 'Reglas no aplicadas' 

    name = fields.Char(string='Nombre',required=True, translate=True)
    note = fields.Text(string='Descripción')
    sequence = fields.Integer(string='Secuencia',required=True, index=True, default=5,
                              help='Use to arrange calculation sequence')
    run_id = fields.Many2one('hr.payslip.run', 'Lote de nomina')
    code = fields.Char(string='Código',required=True)
    slip_id = fields.Many2one('hr.payslip', string='Nómina', required=True, ondelete='cascade')
    salary_rule_id = fields.Many2one('hr.salary.rule', string='Regla', required=True)
    category_id = fields.Many2one(related='salary_rule_id.category_id', string='Categoría',readonly=True, store=True)
    contract_id = fields.Many2one('hr.contract', string='Contrato', required=True, index=True)
    employee_id = fields.Many2one('hr.employee', string='Empleado', required=True)
    entity_id = fields.Many2one('hr.employee.entities', string="Entidad")
    loan_id = fields.Many2one('hr.loan', 'Prestamo', readonly=True)   
    rate = fields.Float(string='Porcentaje (%)', digits='Payroll Rate', default=100.0)
    amount = fields.Float(string='Importe',digits='Payroll')
    quantity = fields.Float(string='Cantidad',digits='Payroll', default=1.0)
    total = fields.Float(compute='_compute_total', string='Total', digits='Payroll', store=True) 
    subtotal =  fields.Float('Subtotal')
    category_code = fields.Char(related='salary_rule_id.category_id.code', string='Categoría',readonly=True, store=True)
    
    @api.depends('quantity', 'amount', 'rate','subtotal')
    def _compute_total(self):
        for line in self:
            if line.subtotal != 0.0:
                line.total = line.subtotal
            else:
                line.total = float(line.quantity) * line.amount * line.rate / 100

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if 'employee_id' not in values or 'contract_id' not in values:
                payslip = self.env['hr.payslip'].browse(values.get('slip_id'))
                values['employee_id'] = values.get('employee_id') or payslip.employee_id.id
                values['contract_id'] = values.get('contract_id') or payslip.contract_id and payslip.contract_id.id
                if not values['contract_id']:
                    raise UserError(_('You must set a contract to create a payslip line.'))
        return super(Hr_payslip_not_line, self).create(vals_list)



class HrPayslipDay(models.Model):
    _name = 'hr.payslip.day'
    _description = 'Días de Nómina'
    _order = 'day'

    payslip_id = fields.Many2one(comodel_name='hr.payslip', string='Nómina', required=True,ondelete='cascade')
    day_type = fields.Selection(string='Tipo', selection=DAY_TYPE)
    day = fields.Integer(string='Día')
    name = fields.Char(string='Nombre', compute="_compute_name")
    subtotal =  fields.Float('Subtotal')
    @api.depends('day', 'day_type')
    def _compute_name(self):
        for record in self:
            record.name = str(record.day) + record.day_type


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'
    
    mail_state = fields.Selection([
        ('draft', 'Pendiente'),
        ('sent', 'Enviado'),
        ('failed', 'Error')
    ], string='Estado Email', default='draft', tracking=True)
    mail_sent_date = fields.Datetime('Fecha Envío')
    mail_error_msg = fields.Text('Error de Envío')

    def action_send_payslip_email(self):
        self.ensure_one()
        if not (self.employee_id.work_email or self.employee_id.personal_email):
            raise UserError(_(f"El empleado {self.employee_id.name} no tiene configurado un correo electrónico."))
        template = self.env.ref('lavish_hr_payroll.email_template_payslip_smart')
        try:
            report = self.struct_id.report_id
            pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(report, self.id)
            attachment = self.env['ir.attachment'].create({
                'name': f"Comprobante_nomina_{self.employee_id.work_contact_id.vat}_{self.name}.pdf",
                'type': 'binary',
                'datas': base64.b64encode(pdf_content),
                'res_model': 'hr.payslip',
                'res_id': self.id,
            })
            template.with_context(
                no_grouped_keys=True,
                force_send=True
            ).send_mail(
                self.id,
                force_send=True,
                email_values={
                    'attachment_ids': [(4, attachment.id)]
                },
                raise_exception=True
            )
            self.write({
                'mail_state': 'sent',
                'mail_sent_date': fields.Datetime.now(),
                'mail_error_msg': False
            })
            self.message_post(body=("Comprobante de nómina enviado por correo electrónico."))
            
        except Exception as e:
            error_msg = str(e)
            self.write({
                'mail_state': 'failed',
                'mail_error_msg': error_msg
            })
            raise UserError(("Error al enviar el correo: %s") % error_msg)