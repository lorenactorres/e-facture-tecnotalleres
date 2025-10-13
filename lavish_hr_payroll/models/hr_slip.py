# -*- coding: utf-8 -*-
from odoo import models, fields, api, _, SUPERUSER_ID , tools
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero, float_round, date_utils
from collections import defaultdict
from datetime import datetime, timedelta, date, time
from odoo.tools.misc import format_date
import calendar
from collections import defaultdict, Counter
from dateutil.relativedelta import relativedelta
import ast
from odoo import api, Command, fields, models, _
from .browsable_object import BrowsableObject, InputLine, WorkedDays, Payslips, ResultRules
from .browsable_object import ResultRules_co
from odoo.exceptions import UserError, ValidationError
from odoo.osv.expression import AND
from odoo.tools import float_round, date_utils, convert_file, html2plaintext, is_html_empty, format_amount
from odoo.tools.float_utils import float_compare
from odoo.tools.misc import format_date
from odoo.tools.safe_eval import safe_eval
from pprint import pformat
import logging
import json
import io
import base64
from decimal import Decimal
import math
#from math import round
_logger = logging.getLogger(__name__)
import re
from psycopg2 import sql
def json_serial(obj):
    """
    Función auxiliar extendida para serializar objetos de Odoo y tipos básicos.
    Maneja fechas, decimales, objetos de Odoo y objetos genéricos con __dict__.
    """
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return float(obj)
    elif hasattr(obj, '_name'):  
        return {
            'id': getattr(obj, 'id', None),
            'name': getattr(obj, 'name', ''),
            'model': getattr(obj, '_name', '')
        }
    elif hasattr(obj, 'name') and callable(getattr(obj, 'name_get', None)):
        return dict(obj.name_get()[0]) if obj.name_get() else str(obj)
    elif hasattr(obj, '__dict__'):
        return {k: v for k, v in obj.__dict__.items() 
                if not k.startswith('_') and not callable(v)}
    raise TypeError(f"Type {type(obj)} not serializable")

class HrPayslip(models.Model):
    _name = 'hr.payslip'
    _inherit = ['hr.payslip', 'sequence.mixin']
    _sequence_index = 'sequence_prefix'
    _sequence_field = 'number'
    _sequence_fixed_regex = r'^(?P<prefix1>.*?)(?P<seq>\d*)(?P<suffix>\D*?)$'
    
    def convert_tuples_to_dict(self,tuple_list):
        data_list = ast.literal_eval(tuple_list)
        return data_list

    def days_between(self,start_date,end_date):
        s1, e1 =  start_date , end_date + timedelta(days=1)
        s360 = (s1.year * 12 + s1.month) * 30 + s1.day
        e360 = (e1.year * 12 + e1.month) * 30 + e1.day
        res = divmod(e360 - s360, 30)
        return ((res[0] * 30) + res[1]) or 0   
    
    @api.depends('struct_id', 'struct_id.process', 'credit_note')
    def _compute_sequence_prefix(self):
        """Compute sequence prefix based on payslip type"""
        for slip in self:
            if slip.struct_id and slip.struct_id.process:
                prefix_map = {
                    'nomina': 'NOM' if not slip.credit_note else 'RNOM',
                    'vacaciones': 'VAC' if not slip.credit_note else 'RVAC',
                    'prima': 'PRI' if not slip.credit_note else 'RPRI',
                    'cesantias': 'CES' if not slip.credit_note else 'RCES',
                    'contrato': 'LIQ' if not slip.credit_note else 'RLIQ',
                    'intereses_cesantias': 'INT' if not slip.credit_note else 'RINT',
                    'otro': 'OTR' if not slip.credit_note else 'ROTR'
                }
                slip.sequence_prefix = f"{prefix_map.get(slip.struct_id.process, 'OTR')}-"
            else:
                slip.sequence_prefix = 'OTR-'

    @api.depends('struct_id', 'struct_id.process', 'credit_note')
    def _compute_move_type(self):
        """Compute move_type based on structure process and reversal status"""
        for slip in self:
            if slip.struct_id:
                process = slip.struct_id.process
                if process == 'nomina':
                    slip.move_type = 'r_payroll' if slip.credit_note else 'payroll'
                elif process == 'vacaciones':
                    slip.move_type = 'r_vacaciones' if slip.credit_note else 'vacaciones'
                elif process == 'prima':
                    slip.move_type = 'r_prima' if slip.credit_note else 'prima'
                elif process == 'cesantias':
                    slip.move_type = 'r_cesantias' if slip.credit_note else 'cesantias'
                elif process == 'contrato':
                    slip.move_type = 'r_liquidacion' if slip.credit_note else 'liquidacion'
                else:
                    slip.move_type = 'r_otros' if slip.credit_note else 'otros'
            else:
                slip.move_type = 'r_payroll' if slip.credit_note else 'payroll'
    
    move_type = fields.Selection([
        ('payroll', 'Nomina'),
        ('prima', 'Prima'),
        ('cesantias', 'Cesantias'),
        ('vacaciones', 'Vacaciones'),
        ('liquidacion', 'Liquidacion Final'),
        ('otros', 'Otros'),
        ('r_payroll', 'Reversion de Nomina'),
        ('r_prima', 'Reversion Prima'),
        ('r_cesantias', 'Reversion Cesantias'),
        ('r_vacaciones', 'Reversion Vacaciones'),
        ('r_liquidacion', 'Reversion Liquidacion'),
        ('r_otros', 'Reversion Otros')
    ], string='Tipo de documento', compute='_compute_move_type', store=True)
    number = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='/')
    reversed_slip_id = fields.Many2one('hr.payslip', string='Reversed Payslip', readonly=True, copy=False)
    sequence_prefix = fields.Char(compute='_compute_sequence_prefix', store=True)
    sequence_number = fields.Integer(compute='_compute_split_sequence', store=True)
    leave_ids = fields.One2many('hr.absence.days', 'payroll_id', string='Novedades', readonly=True)
    leave_days_ids =fields.One2many('hr.leave.line', 'payslip_id', string='Detalle de Ausencia', readonly=True)
    payslip_day_ids = fields.One2many(comodel_name='hr.payslip.day', inverse_name='payslip_id', string='Días de Nómina', readonly=True)
    rtefte_id = fields.Many2one('hr.employee.rtefte', 'RteFte', readonly=True)
    not_line_ids = fields.One2many('hr.payslip.not.line', 'slip_id', string='Reglas no aplicadas', readonly=True)
    observation = fields.Text(string='Observación')
    analytic_account_id = fields.Many2one('account.analytic.account', string='Cuenta analítica')
    struct_process = fields.Selection(related='struct_id.process', string='Proceso', store=True)
    employee_branch_id = fields.Many2one(related='employee_id.branch_id', string='Sucursal empleado', store=True)
    definitive_plan = fields.Boolean(string='Plano definitivo generado')
    #Fechas liquidación de contrato
    date_liquidacion = fields.Date('Fecha liquidación de contrato')
    date_prima = fields.Date('Fecha liquidación de prima')
    date_cesantias = fields.Date('Fecha liquidación de cesantías')
    date_vacaciones = fields.Date('Fecha liquidación de vacaciones')
    worked_days_line_ids = fields.One2many('hr.payslip.worked_days', 'payslip_id', compute=False, )
    pay_cesantias_in_payroll = fields.Boolean('¿Liquidar Interese de cesantia periodo anterior en nómina ?')
    pay_primas_in_payroll = fields.Boolean('¿Liquidar Primas en nómina?')
    pay_vacations_in_payroll = fields.Boolean('¿Liquidar vacaciones en nómina?')
    provisiones = fields.Boolean('Provisiones')
    journal_struct_id = fields.Many2one('account.journal', string='Salary Journal', domain="[('company_id', '=', company_id)]")
    earnings_ids = fields.One2many(comodel_name='hr.payslip.line', compute="_compute_concepts_category", string='Conceptos de Nómina / Devengos')
    deductions_ids = fields.One2many(comodel_name='hr.payslip.line', compute="_compute_concepts_category", string='Conceptos de Nómina / Deducciones')
    bases_ids = fields.One2many(comodel_name='hr.payslip.line', compute="_compute_concepts_category", string='Conceptos de Nómina / Bases')
    provisions_ids = fields.One2many(comodel_name='hr.payslip.line', compute="_compute_concepts_category", string='Conceptos de Nómina / Provisiones')
    outcome_ids = fields.One2many(comodel_name='hr.payslip.line', compute="_compute_concepts_category", string='Conceptos de Nómina / Totales')
    date_from = fields.Date(
        string='From', readonly=False, required=True, tracking=True,
        compute=False, store=True, precompute=False)
    date_to = fields.Date(
        string='To', readonly=False, required=True, tracking=True,
        compute=False, store=True, precompute=False)
    periodo = fields.Char('Periodo', compute="_periodo", store=True)
    extrahours_ids = fields.One2many('hr.overtime', 'payslip_run_id',  string='Horas Extra Detallada', )
    novedades_ids = fields.One2many('hr.novelties.different.concepts', 'payslip_id',  string='Novedades Detalladas')
    payslip_old_ids = fields.Many2many('hr.payslip', 'hr_payslip_rel', 'current_payslip_id', 'old_payslip_id', string='Nominas relacionadas')
    resulados_op = fields.Html('Resultados')
    resulados_rt = fields.Html('Resultados RT')
    payslip_detail = fields.Html(compute='_compute_payslip_detail')
    prestaciones_sociales_report = fields.Html(string="Reporte de Prestaciones Sociales", compute='_compute_prestaciones_sociales_report')
    reason_retiro = fields.Many2one('hr.departure.reason', string='Motivo de retiro')
    have_compensation = fields.Boolean('Indemnización', default=False)
    settle_payroll_concepts = fields.Boolean('Liquida conceptos de nómina', default=True)
    novelties_payroll_concepts = fields.Boolean('Liquida conceptos de novedades', default=True)
    pagar_cesantias_ano_anterior = fields.Boolean('Liquida conceptos de Cesantia periodo anterior', default=True)
    no_days_worked = fields.Boolean('Sin días laborados', default=False, help='Aplica unicamente cuando la fecha de inicio es igual a la fecha de finalización.')
    paid_vacation_ids = fields.One2many('hr.payslip.paid.vacation', 'slip_id',string='Vacaciones remuneradas')
    refund_date = fields.Date(string='Fecha reintegro')
    is_advance_severance = fields.Boolean(string='Es avance de cesantías')
    value_advance_severance = fields.Float(string='Valor a pagar avance')
    employee_severance_pay = fields.Boolean(string='Pago cesantías al empleado')
    severance_payments_reverse = fields.Many2many('hr.history.cesantias',
                                                  string='Historico de cesantias/int.cesantias a tener encuenta',
                                                  domain="[('employee_id', '=', employee_id)]")
    prima_run_reverse_id = fields.Many2one('hr.payslip.run', string='Lote de prima a ajustar')
    prima_payslip_reverse_id = fields.Many2one('hr.payslip', string='Prima a ajustar', domain="[('employee_id', '=', employee_id)]")
    rule_override_ids = fields.One2many('hr.payslip.rule.override', 'payslip_id', 'Ajustes de Reglas')
    has_overrides = fields.Boolean('Tiene Ajustes', compute='_compute_has_overrides')
    enable_rule_overrides = fields.Boolean(
        'Habilitar ajustes manuales', 
        help='Permite modificar manualmente los valores de las reglas salariales',
        tracking=True
    )
    contract_info = fields.Html()
    @api.depends('rule_override_ids.active')
    def _compute_has_overrides(self):
        for record in self:
            record.has_overrides = bool(record.rule_override_ids.filtered('active'))

    @api.onchange('enable_rule_overrides')
    def _onchange_enable_rule_overrides(self):
        if self.enable_rule_overrides:
            message = _("""ADVERTENCIA: Ha activado el modo de ajustes manuales.
            
            Tenga en cuenta que:
            - Los ajustes manuales pueden crear diferencias con los cálculos automáticos
            - Estas modificaciones quedarán registradas en el historial
            - Se recomienda documentar el motivo de cada ajuste
            - Los totales de nómina pueden variar significativamente
            - Las novedades se debe ajustar directamente, desde su modulo
            Asegúrese de validar todos los cálculos antes de confirmar la nómina.""")
            
            return {
                'warning': {
                    'title': _("Modo de Ajustes Manuales"),
                    'message': message
                }
            }

    def init(self):
        if not self._abstract and self._sequence_index:
            index_name = self._table + '_sequence_index'
            self.env.cr.execute('SELECT indexname FROM pg_indexes WHERE indexname = %s', (index_name,))
            if not self.env.cr.fetchone():
                self.env.cr.execute(sql.SQL("""
                    CREATE INDEX {index_name} ON {table} ({sequence_index}, sequence_prefix desc, sequence_number desc, {field});
                    CREATE INDEX {index2_name} ON {table} ({sequence_index}, id desc, sequence_prefix);
                """).format(
                    sequence_index=sql.Identifier(self._sequence_index),
                    index_name=sql.Identifier(index_name),
                    index2_name=sql.Identifier(index_name + "2"),
                    table=sql.Identifier(self._table),
                    field=sql.Identifier(self._sequence_field),
                ))

    def _get_last_sequence_domain(self, relaxed=False):
        self.ensure_one()
        where_string = "WHERE sequence_prefix = %(sequence_prefix)s"
        param = {'sequence_prefix': self.sequence_prefix}
        return where_string, param

    def _get_starting_sequence(self):
        """ Returns the initial sequence for the given document type """
        self.ensure_one()
        return f"{self.sequence_prefix}00001"

    def _compute_split_sequence(self):
        """Compute the sequence number"""
        for record in self:
            sequence = record[record._sequence_field] or ''
            regex = re.sub(r"\?P<\w+>", "?:", record._sequence_fixed_regex.replace(r"?P<seq>", ""))
            matching = re.match(regex, sequence)
            if matching:
                record.sequence_number = int(matching.group(1) or 0)
            else:
                record.sequence_number = 0
    @api.depends('sequence_prefix', 'sequence_number')
    def _compute_name(self):
        """Compute the full name based on prefix and sequence number"""
        for record in self:
            if record.sequence_number:
                record.number = f'{record.sequence_prefix}{record.sequence_number:05d}'

    def _sequence_matches_date(self):
        """Override to always return True since we don't use date in sequence"""
        return True
    
    def _set_next_sequence(self):
        """Set the next sequence.
        This method ensures that the sequence is set both in the ORM and in the database.
        """
        self.ensure_one()

        # Obtener la última secuencia
        last_sequence = self._get_last_sequence()
        new = not last_sequence
        if new:
            last_sequence = self._get_starting_sequence()

        format_string = "{prefix1}{seq:05d}"
        sequence_number = 1

        if not new:
            match = re.match(self._sequence_fixed_regex, last_sequence)
            if match:
                sequence_number = int(match.group('seq') or 0) + 1

        self[self._sequence_field] = format_string.format(
            prefix1=self.sequence_prefix, 
            seq=sequence_number
        )
        self._compute_split_sequence()

    def _get_sequence_format_param(self, previous):
        """Get format parameters for the sequence"""
        if not previous or not re.match(self._sequence_fixed_regex, previous):
            return "{prefix1}{seq:05d}{suffix}", {
                'prefix1': self.sequence_prefix,
                'seq': 0,
                'seq_length': 5,
                'suffix': ''
            }

        format_values = re.match(self._sequence_fixed_regex, previous).groupdict()
        format_values['seq_length'] = 5
        format_values['seq'] = int(format_values.get('seq') or 0)
            
        if not format_values.get('prefix1'):
            format_values['prefix1'] = self.sequence_prefix
        if not format_values.get('suffix'):
            format_values['suffix'] = ''
                
        return "{prefix1}{seq:0{seq_length}d}{suffix}", format_values

    @api.onchange('struct_id', 'credit_note')
    def onchange_struct_id(self):
        """Update move_type when structure or reversal status changes"""
        self._compute_move_type()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('number', '/') == '/':
                vals['number'] = '/'
        return super().create(vals_list)

    def write(self, vals):
        """Handle sequence updates on write"""
        if 'struct_id' in vals or 'credit_note' in vals:
            self._compute_move_type()
        if vals.get(self._sequence_field):
            self._compute_split_sequence()
        return super().write(vals)


    @api.onchange('employee_id','contract_id','struct_id','date_to')
    def load_dates_liq_contrato(self):
        if self.struct_id.process == 'contrato':
            self.date_liquidacion = self.date_to
            
            # Prima (Bonus) Date
            date_prima = self.contract_id.date_start
            obj_prima = self.env['hr.history.prima'].search([
                ('employee_id', '=', self.employee_id.id),
                ('contract_id', '=', self.contract_id.id)
            ])
            if obj_prima:
                date_prima = max(
                    (history.final_accrual_date + timedelta(days=1) for history in sorted(obj_prima, key=lambda x: x.final_accrual_date)), 
                    default=self.contract_id.date_start
                )
            self.date_prima = date_prima

            # Vacation Date
            date_vacation = self.contract_id.date_start
            obj_vacation = self.env['hr.vacation'].search([
                ('employee_id', '=', self.employee_id.id),
                ('contract_id', '=', self.contract_id.id)
            ])
            if obj_vacation:
                date_vacation = max(
                    (history.final_accrual_date + timedelta(days=1) 
                    for history in sorted(obj_vacation, key=lambda x: x.final_accrual_date)
                    if not history.leave_id or (history.leave_id and not history.leave_id.holiday_status_id.unpaid_absences)),
                    default=self.contract_id.date_start
                )
            self.date_vacaciones = date_vacation

            # Cesantias (Severance) Date
            date_cesantias = self.contract_id.date_start
            obj_cesantias = self.env['hr.history.cesantias'].search([
                ('employee_id', '=', self.employee_id.id),
                ('contract_id', '=', self.contract_id.id)
            ])
            if obj_cesantias:
                date_cesantias = max(
                    (history.final_accrual_date + timedelta(days=1) for history in sorted(obj_cesantias, key=lambda x: x.final_accrual_date)), 
                    default=self.contract_id.date_start
                )
            self.date_cesantias = date_cesantias

    @api.depends('line_ids.computation')
    def _compute_prestaciones_sociales_report(self):
        for payslip in self:
            prestaciones_lines = payslip.line_ids.filtered(lambda line: line.computation and line.salary_rule_id.code not in ('IBD','IBC_R','RT_MET_01'))
            if prestaciones_lines:
                all_reports = []
                for line in prestaciones_lines:
                    try:
                        computation_data = json.loads(line.computation)
                        report = self._generate_formatted_prestaciones_report(line, computation_data)
                        all_reports.append(report)
                    except json.JSONDecodeError:
                        all_reports.append(f'<p>Error al procesar los datos de la línea {line.name}.</p>')
                
                payslip.prestaciones_sociales_report = self._combine_reports(all_reports)
            else:
                payslip.prestaciones_sociales_report = '<p>No hay datos de prestaciones sociales disponibles.</p>'

    def _format_reporte_html(self, data):
        """
        Genera el reporte en formato HTML para campos html de Odoo
        Args:
            data: Lista o diccionario con los datos del reporte
        Returns:
            str: Reporte HTML formateado
        """
        def format_currency(value):
            try:
                return f"${value:,.0f}" if value else "$0"
            except:
                return "$0"

        def format_section(title, content):
            return f"""
                <div class="section-container" style="margin-bottom: 15px;">
                    <div class="section-title" style="background-color: #C41E3A; color: white; padding: 8px; font-weight: bold;">
                        {title}
                    </div>
                    <div class="section-content" style="border: 1px solid #ddd; padding: 10px;">
                        {content}
                    </div>
                </div>
            """

        def format_row(label, value, observation=None, limit=None):
            limit_text = f'<div style="color: #0066cc; text-align: right; font-size: 0.9em;">Límite: {limit}</div>' if limit else ''
            obs_text = f'<div style="color: #C41E3A; font-size: 0.9em;">{observation}</div>' if observation else ''
            return f"""
                <div style="display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #eee;">
                    <div style="flex: 2;">
                        {label}
                        {obs_text}
                        {limit_text}
                    </div>
                    <div style="flex: 1; text-align: right; font-weight: bold;">
                        {value}
                    </div>
                </div>
            """

        try:
            if isinstance(data, list):
                data = data[0] if data else {}
            
            if not data:
                return "<div>No hay datos disponibles para mostrar</div>"

            html = f"""
            <div style="font-family: Arial, sans-serif; font-size: 13px;">
                <div style="background-color: #C41E3A; color: white; padding: 10px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                    <div style="font-size: 16px; font-weight: bold;">RETENCIÓN EN LA FUENTE MENSUAL</div>
                    <div>Valor UVT: {format_currency(data.get('uvt', 0))}</div>
                </div>
            """

            ingresos = data.get('ingresos', {})
            ingresos_content = "".join([
                format_row("Sueldo básico", format_currency(ingresos.get('salario', 0))),
                format_row("Comisiones", format_currency(ingresos.get('comisiones', 0))),
                format_row("Otros pagos laborales", format_currency(ingresos.get('otros_ingresos', 0))),
                format_row("Total Ingresos Laborales", format_currency(ingresos.get('total', 0)))
            ])
            html += format_section("1. PAGOS LABORALES DEL MES", ingresos_content)

            aportes = data.get('aportes_obligatorios', {})
            no_renta_content = "".join([
                format_row("Aportes obligatorios a Pensión", format_currency(aportes.get('pension', 0))),
                format_row("Aportes obligatorios a Salud", format_currency(aportes.get('salud', 0))),
                format_row("Total Ingresos No Constitutivos", format_currency(aportes.get('total', 0))),
                format_row("Subtotal 1", format_currency(data.get('base_calculo', {}).get('subtotal_1', 0)))
            ])
            html += format_section("2. INGRESOS NO CONSTITUTIVOS DE RENTA", no_renta_content)

            deducciones = data.get('deducciones', {})
            deducciones_content = "".join([
                format_row(
                    "Intereses de vivienda", 
                    format_currency(deducciones.get('vivienda', 0)),
                    "Límite máximo 100 UVT Mensuales",
                    format_currency(deducciones.get('limite_vivienda', 0))
                ),
                format_row(
                    "Dependientes", 
                    format_currency(deducciones.get('dependientes', 0)),
                    "No puede exceder del 10% del ingreso bruto y máximo 32 UVT mensuales",
                    format_currency(deducciones.get('limite_dependientes', 0))
                ),
                format_row(
                    "Medicina prepagada", 
                    format_currency(deducciones.get('salud_prepagada', 0)),
                    "No puede exceder 16 UVT Mensuales",
                    format_currency(deducciones.get('limite_salud', 0))
                ),
                format_row("Total Deducciones", format_currency(deducciones.get('total', 0)))
            ])
            html += format_section("3. DEDUCCIONES", deducciones_content)

            rentas = data.get('rentas_exentas', {})
            rentas_content = "".join([
                format_row(
                    "Aportes AFC", 
                    format_currency(rentas.get('afc', 0)),
                    "Límite del 30% del ingreso laboral y hasta 3.800 UVT anuales",
                    format_currency(rentas.get('limite_afc', 0))
                ),
                format_row(
                    "Renta Exenta 25%", 
                    format_currency(rentas.get('renta_exenta_25', 0)),
                    None,
                    format_currency(rentas.get('limite_renta_25', 0))
                ),
                format_row("Total Rentas Exentas", format_currency(rentas.get('total', 0)))
            ])
            html += format_section("4. RENTAS EXENTAS", rentas_content)

            base_calculo = data.get('base_calculo', {})
            retencion = data.get('retencion', {})
            base_content = "".join([
                format_row("Base Gravable en UVTs", f"{base_calculo.get('base_uvts', 0):,.2f}"),
                format_row("Porcentaje de Retención", f"{retencion.get('tarifa', 0)}%"),
                format_row("Retención calculada", format_currency(retencion.get('valor', 0))),
                format_row("Retención anterior", format_currency(retencion.get('anterior', 0))),
                format_row("Retención definitiva", format_currency(retencion.get('definitiva', 0)))
            ])
            html += format_section("5. BASE GRAVABLE Y RETENCIÓN", base_content)

            html += f"""
                <div style="background-color: #fff3cd; border: 1px solid #ffeeba; padding: 10px; margin-top: 15px; font-size: 0.9em;">
                    <strong>NOTA IMPORTANTE:</strong><br>
                    La sumatoria de las Deducciones, Rentas exentas y el 25% de la renta de trabajo exenta,
                    no podrá superar el 40% del ingreso señalado en el subtotal 1 hasta 1340 UVT
                </div>
            """

            html += "</div>"
            return html
            
        except Exception as e:
            return f"<div>Error al generar el reporte: {str(e)}</div>"


    def generate_ibd_html_report(self, data):
        """
        Genera el reporte HTML para el cálculo del IBC
        Args:
            data: Diccionario con los datos del cálculo
        Returns:
            str: Reporte HTML formateado
        """
        html = """
        <div style="font-family: Arial, sans-serif; padding: 15px;">
            <div style="background-color: #4a90e2; color: white; padding: 10px; margin-bottom: 20px;">
                <h2 style="margin: 0;">Ingreso Base de Cotización (IBC)</h2>
            </div>
        """

        # Sección de información técnica
        html += self._format_info_box(
            "Base Técnica del Cálculo",
            f"""
            <div style="line-height: 1.6;">
                <strong>Base Legal:</strong> Artículo 127 del CST - Elementos Integrantes
                <div style="margin-top: 10px;">
                    <strong>Componentes del IBC:</strong>
                    <ul style="list-style-type: none; padding-left: 15px; margin-top: 5px;">
                        <li>• Salario básico mensual</li>
                        <li>• Comisiones y bonificaciones salariales</li>
                        <li>• Horas extras y recargos</li>
                        <li>• Otros pagos constitutivos de salario</li>
                    </ul>
                </div>
                <div style="margin-top: 10px;">
                    <strong>Topes Legales:</strong>
                    <ul style="list-style-type: none; padding-left: 15px; margin-top: 5px;">
                        <li>• Mínimo: 1 SMMLV</li>
                        <li>• Máximo: 25 SMMLV ({self._format_currency(data['topes']['tope_25_smmlv'])})</li>
                        {
                            '<li>• Salario Integral: 70% del salario total</li>'
                            if data.get('contrato_integral') else ''
                        }
                    </ul>
                </div>
            </div>
            """
        )

        # IBC Anterior
        html += self._format_info_box(
            "IBC Anterior",
            self._format_currency(data.get('ibc_anterior', 0)),
            "background-color: #e8f4ff; border-color: #b8daff;"
        )

        # Tabla de Ingresos
        html += """
            <div style="margin-top: 20px;">
                <h3 style="margin-bottom: 10px;">Detalle de Ingresos del Periodo</h3>
                <table style="width: 100%; border-collapse: collapse;">
        """

        # Encabezado de la tabla
        html += self._format_table_header([
            "Concepto",
            "Valor",
            "Fecha",
            "Categoría",
            "Tipo"
        ])

        # Separar ingresos por tipo
        ingresos_salariales = []
        ingresos_no_salariales = []
        for code, info in data['ingresos']['detalle'].items():
            if info['tipo'] == 'salarial':
                ingresos_salariales.append((code, info))
            else:
                ingresos_no_salariales.append((code, info))

        # Sección Ingresos Salariales
        html += self._format_section_header("Ingresos Salariales")
        for code, info in ingresos_salariales:
            html += self._format_table_row([
                {"value": info['name'], "align": "left"},
                {"value": self._format_currency(info['valor']), "align": "right"},
                {"value": self._format_fecha(info), "align": "center"},
                {"value": self._format_category_badge(info['categoria'], info['categoria_padre']), "align": "center"},
                {"value": info['tipo'].title(), "align": "center"}
            ])

        # Sección Ingresos No Salariales
        html += self._format_section_header("Ingresos No Salariales")
        for code, info in ingresos_no_salariales:
            html += self._format_table_row([
                {"value": info['name'], "align": "left"},
                {"value": self._format_currency(info['valor']), "align": "right"},
                {"value": self._format_fecha(info), "align": "center"},
                {"value": self._format_category_badge(info['categoria'], info['categoria_padre']), "align": "center"},
                {"value": info['tipo'].title(), "align": "center"}
            ])

        # Totales
        html += self._format_table_row([
            {"value": "Total Ingresos Salariales", "align": "left"},
            {"value": self._format_currency(data['ingresos']['salariales']['total']), "align": "right"},
            {"value": "", "align": "center"},
            {"value": "", "align": "center"},
            {"value": "", "align": "center"}
        ], "background-color: #e6f3ff; font-weight: bold;")

        html += self._format_table_row([
            {"value": "Total Ingresos No Salariales", "align": "left"},
            {"value": self._format_currency(data['ingresos']['no_salariales']['total']), "align": "right"},
            {"value": "", "align": "center"},
            {"value": "", "align": "center"},
            {"value": "", "align": "center"}
        ], "background-color: #fff3e0; font-weight: bold;")

        html += """
                </table>
            </div>
        """

        # Sección de Topes
        html += """
            <div style="margin-top: 20px;">
                <h3 style="margin-bottom: 10px;">Aplicación de Topes</h3>
                <table style="width: 100%; border-collapse: collapse;">
        """

        html += self._format_table_header([
            "Concepto",
            "Valor",
            "Aplicado"
        ])

        html += self._format_table_row([
            {"value": "Tope 25 SMMLV", "align": "left"},
            {"value": self._format_currency(data['topes']['tope_25_smmlv']), "align": "right"},
            {"value": self._format_status_indicator(data['topes']['supera_tope']), "align": "center"}
        ])
        html += self._format_table_row([
            {"value": "Tope 40% No Salarial", "align": "left"},
            {"value": self._format_currency(data['topes']['tope_40']['valor']), "align": "right"},
            {"value": self._format_status_indicator(data['topes']['tope_40']['aplicado']), "align": "center"}
        ])
        html += """
                </table>
            </div>
        """

        # IBC Final
        html += """
            <div style="background-color: #28a745; color: white; padding: 15px; border-radius: 4px; margin-top: 20px;">
                <div style="font-size: 18px; font-weight: bold;">IBC Final</div>
                <div style="font-size: 24px; margin-top: 5px;">{}</div>
            </div>
        """.format(self._format_currency(data['amount']))

        if data.get('contrato_integral'):
            html += self._format_info_box(
                "Nota",
                "Se aplicó factor del 70% por ser contrato integral",
                "background-color: #fff3cd; border-color: #ffeeba;"
            )

        html += "</div>"
        return html

    def _format_fecha(self, info):
        """
        Formatea la fecha o fechas de un registro
        Args:
            info: Diccionario con la información del registro
        Returns:
            str: Fechas formateadas
        """
        if 'fecha' in info:
            return self._format_date(info['fecha'])
        elif 'fechas' in info:
            return "<br>".join(
                f"{self._format_date(f['from'])} - {self._format_date(f['to'])}" 
                for f in info['fechas']
            )
        return ""

    def _format_currency(self, value):
        """
        Formatea un valor como moneda
        Args:
            value: valor a formatear
        Returns:
            str: valor formateado como moneda
        """
        try:
            return f"${value:,.2f}" if value else "$0"
        except:
            return "$0"

    def _format_date(self, date):
        """
        Formatea una fecha en formato dd/mm/yyyy
        Args:
            date: fecha a formatear
        Returns:
            str: fecha formateada
        """
        return date.strftime('%d/%m/%Y') if date else ''

    def _format_section_header(self, title):
        """
        Formatea el encabezado de una sección
        Args:
            title: título de la sección
        Returns:
            str: HTML del encabezado
        """
        return f"""
            <tr style="background-color: #e6f3ff;">
                <td colspan="5" style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">
                    {title}
                </td>
            </tr>
        """

    def _format_table_header(self, columns):
        """
        Formatea el encabezado de una tabla
        Args:
            columns: lista de nombres de columnas
        Returns:
            str: HTML del encabezado
        """
        header_cells = "".join([
            f'<th style="padding: 8px; border: 1px solid #ddd; text-align: center;">{col}</th>'
            for col in columns
        ])
        return f"""
            <tr style="background-color: #f5f5f5;">
                {header_cells}
            </tr>
        """

    def _format_table_row(self, cells, style=""):
        """
        Formatea una fila de tabla
        Args:
            cells: lista de valores de celda
            style: estilo adicional para la fila
        Returns:
            str: HTML de la fila
        """
        row_cells = "".join([
            f'<td style="padding: 8px; border: 1px solid #ddd; text-align: {cell.get("align", "left")};">{cell["value"]}</td>'
            for cell in cells
        ])
        return f"""
            <tr style="{style}">
                {row_cells}
            </tr>
        """

    def _format_info_box(self, title, content, style=""):
        """
        Formatea una caja de información
        Args:
            title: título de la caja
            content: contenido de la caja
            style: estilo adicional
        Returns:
            str: HTML de la caja de información
        """
        return f"""
            <div style="margin: 15px 0; padding: 12px; background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 4px; {style}">
                <strong>{title}</strong>
                <div style="margin-top: 8px;">
                    {content}
                </div>
            </div>
        """

    def _format_totals_section(self, data):
        """
        Formatea la sección de totales
        Args:
            data: diccionario con los totales
        Returns:
            str: HTML de la sección de totales
        """
        return f"""
            <div style="margin-top: 20px; padding: 15px; background-color: #e9ecef; border-radius: 4px;">
                <table style="width: 100%;">
                    <tr>
                        <td style="padding: 4px;"><strong>Total Ingresos Base:</strong></td>
                        <td style="text-align: right;">{self._format_currency(data.get('total_ingresos', 0))}</td>
                    </tr>
                    <tr>
                        <td style="padding: 4px;"><strong>IBC Final:</strong></td>
                        <td style="text-align: right;">{self._format_currency(data.get('ibc_final', 0))}</td>
                    </tr>
                </table>
            </div>
        """

    def _format_category_badge(self, category, parent_category=None):
        """
        Formatea un badge de categoría
        Args:
            category: código de la categoría
            parent_category: código de la categoría padre
        Returns:
            str: HTML del badge
        """
        badge = category
        if parent_category:
            badge += f' <span style="font-size: 0.9em; color: #666;">({parent_category})</span>'
        
        return f'<span style="padding: 2px 6px; border-radius: 3px; background-color: #e9ecef;">{badge}</span>'

    def _format_status_indicator(self, is_active):
        """
        Formatea un indicador de estado
        Args:
            is_active: bool indicando si está activo
        Returns:
            str: HTML del indicador
        """
        color = "#28a745" if is_active else "#dc3545"
        return f'<span style="color: {color};">{"✓" if is_active else "✗"}</span>'

    def _generate_formatted_prestaciones_report(self, line, prestaciones_data):
        html_report = [
            '<!DOCTYPE html>',
            '<html lang="es">',
            '<head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            '<title>Reporte de Prestaciones</title>',
            '<style>',
            'body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 10px; font-size: 12px; }',
            'table { width: 100%; border-collapse: collapse; margin-bottom: 10px; font-size: 12px; border: 1px solid #333; }',
            'th, td { padding: 4px; border: 1px solid #333; }',
            'th { background-color: #eee; }',
            'h2 { color: #333; padding-bottom: 5px; }',
            'h3 { color: #444; margin-top: 15px; margin-bottom: 5px; }',
            '.section-content { margin-left: 10px; }',
            '</style>',
            '</head>',
            '<body>',
            f'<div class="prestaciones-sociales-report" id="line-{line.id}">'
        ]
        html_report.append(f'<h2>Reporte de Prestaciones: {line.name}</h2>')
        meta_info = prestaciones_data.get('meta_info', {})
        tipo_prestacion = meta_info.get("tipo_prestacion", "N/A")
        tipo_prestacion = meta_info.get("tipo_prestacion", "N/A")
        code = meta_info.get("code", "N/A")
        plain_days = meta_info.get("plain_days", 0)
        susp = meta_info.get("susp", 0)
        wage = meta_info.get("wage", 0)
        auxtransporte = meta_info.get("auxtransporte", 0)
        total_variable = meta_info.get("total_variable", 0)
        amount_base = meta_info.get("amount_base", 0)
        valor_pagar = meta_info.get("valor_primas", 0)
        fecha_inicio = meta_info.get("fecha_inicio", "N/A")
        fecha_fin = meta_info.get("fecha_fin", "N/A")
        meta_info = prestaciones_data.get('meta_info', {})
        # Fórmulas específicas
        if code == "INTCESANTIAS":
            rate = 0.12 * plain_days / 360
            formula_usada = f"({wage:,.2f} + {auxtransporte:,.2f}) + ({total_variable:,.2f} / {plain_days}) * 30"
            valor_pagar = wage * rate
            formula_valor_pagar = f"{amount_base:,.2f} * (0.12 * {plain_days} / 360) ="
            formula_valor_pagar += f" -> Tasa: {rate:.5f}%"
        else:
            # Fórmula general
            formula_usada = f"({wage:,.2f} + {auxtransporte:,.2f}) + ({total_variable:,.2f} / {plain_days}) * 30"
            formula_valor_pagar = f"({amount_base:,.2f} / 360) * {plain_days}"

        html_report.extend([
            '<h3>Meta Información</h3>',
            '<table class="table table-sm">',
            '<tr><th>Campo</th><th>Valor</th></tr>',
            f'<tr><td>Tipo de Prestación</td><td>{tipo_prestacion}</td></tr>',
            f'<tr><td>Período</td><td>{fecha_inicio} a {fecha_fin}</td></tr>',
            f'<tr><td>Días a liquidar</td><td style="text-align: right;">{plain_days}</td></tr>',
            f'<tr><td>Días no pagables</td><td style="text-align: right;">{susp}</td></tr>',
            f'<tr><td>Salario</td><td style="text-align: right;">${wage:,.2f}</td></tr>',
            f'<tr><td>Auxilio de Transporte</td><td style="text-align: right;">${auxtransporte:,.2f}</td></tr>',
            f'<tr><td>Total Variable</td><td style="text-align: right;">${total_variable:,.2f}</td></tr>',
            f'<tr><td>Fórmula usada</td><td style="text-align: right;">{formula_usada}</td></tr>',
            f'<tr><td>Monto promedio</td><td style="text-align: right;">${amount_base:,.2f}</td></tr>',
            f'<tr><td>Fórmula para Monto a pagar</td><td style="text-align: right;">{formula_valor_pagar}</td></tr>',
            f'<tr><td>Valor a Pagar</td><td style="text-align: right;">${valor_pagar:,.2f}</td></tr>',
            '</table>'
        ])

        # Variaciones de Salario
        variaciones = prestaciones_data.get('variaciones_salario', [])
        html_report.append('<h3>Variaciones de Salario</h3>')
        if variaciones:
            html_report.extend([
                '<table class="table table-sm">',
                '<tr><th>Fecha</th><th>Salario</th></tr>'
            ])
            for variacion in variaciones:
                html_report.append(
                    f'<tr>'
                    f'<td style="text-align: center;">{variacion["fecha"]}</td>'
                    f'<td style="text-align: right;">${variacion["salario"]:,.2f}</td>'
                    f'</tr>'
                )
            html_report.append('</table>')
        else:
            html_report.append('<p>No se registraron variaciones de salario en este período.</p>')

        # Licencias No Remuneradas
        licencias = prestaciones_data.get('licencias_no_remuneradas', [])
        html_report.append('<h3>Licencias No Remuneradas</h3>')
        if licencias:
            html_report.extend([
                '<table class="table table-sm">',
                '<thead>',
                '<tr><th>Tipo de Licencia</th><th>Fecha Inicio</th><th>Fecha Fin</th><th>Días</th></tr>',
                '</thead>',
                '<tbody>'
            ])
            total_dias = 0
            for licencia in licencias:
                total_dias += licencia["dias"]
                html_report.append(
                    f'<tr>'
                    f'<td>{licencia["tipo_licencia"]}</td>'
                    f'<td style="text-align: center;">{licencia["fecha_inicio"]}</td>'
                    f'<td style="text-align: center;">{licencia["fecha_fin"]}</td>'
                    f'<td style="text-align: right;">{licencia["dias"]}</td>'
                    f'</tr>'
                )
            html_report.extend([
                '</tbody>',
                '<tfoot>',
                f'<tr><td colspan="3" style="text-align: right; font-weight: bold;">Total Días</td>'
                f'<td style="text-align: right; font-weight: bold;">{total_dias}</td></tr>',
                '</tfoot>',
                '</table>'
            ])
        else:
            html_report.append('<p>No se registraron licencias no remuneradas en este período.</p>')

        # Novedades Promedio
        novedades = prestaciones_data.get('novedades_promedio', {})
        html_report.append('<h3>Novedades Promedio</h3>')
        if novedades:
            html_report.extend([
                '<h4>Entradas:</h4>',
                '<table class="table table-sm">',
                '<thead>',
                '<tr><th>Nombre Regla</th><th>Monto</th><th>Fecha</th><th>Origen</th></tr>',
                '</thead>',
                '<tbody>'
            ])
            total_monto = 0
            for entry in novedades.get('entradas', []):
                total_monto += entry["monto"]
                html_report.append(
                    f'<tr>'
                    f'<td>{entry["nombre_regla"]}</td>'
                    f'<td style="text-align: right;">${entry["monto"]:,.2f}</td>'
                    f'<td style="text-align: center;">{entry["fecha"]}</td>'
                    f'<td style="text-align: center;">{entry["origen"]}</td>'
                    f'</tr>'
                )
            # Obtener los totales
            totales = novedades.get('totales', {})
            payslip_total = totales.get("payslip_total", 0)
            accumulated_total = totales.get("accumulated_total", 0)
            total_novedades = totales.get("total_novedades", 0)

            html_report.extend([
                '</tbody>',
                '<tfoot>',
                f'<tr><td style="font-weight: bold;">Total Nómina</td><td style="text-align: right;">${payslip_total:,.2f}</td><td></td><td></td></tr>',
                f'<tr><td style="font-weight: bold;">Total Acumulados</td><td style="text-align: right;">${accumulated_total:,.2f}</td><td></td><td></td></tr>',
                f'<tr><td style="font-weight: bold;">Total Novedades</td><td style="text-align: right;">${total_novedades:,.2f}</td><td></td><td></td></tr>',
                '</tfoot>',
                '</table>'
            ])
        else:
            html_report.append('<p>No se registraron novedades en este período.</p>')

        html_report.extend([
            '</div>',
            '</body>',
            '</html>'
        ])

        return ''.join(html_report)

    def _combine_reports(self, reports):
        combined_html = '<div class="prestaciones-sociales-combined-report">'
        combined_html += '<h1>Reporte de Prestaciones Sociales</h1>'
        for report in reports:
            combined_html += report
            combined_html += '<hr>'  # Separador entre reportes
        combined_html += '</div>'
        return combined_html

    @api.depends('line_ids', 'leave_ids', 'worked_days_line_ids')
    def _compute_payslip_detail(self):
        for payslip in self:
            payslip.payslip_detail = 'Calculated'

    def _periodo(self):
        for rec in self:
            if rec.date_to:
                rec.periodo = rec.date_to.strftime("%Y%m")
            else:
                rec.periodo = ''
    
    def old_payslip_moth(self):
        payslip_objs = self.env['hr.payslip'].search([('struct_id.process', 'in', ['vacaciones', 'prima'])])
        for record in self:
            record.payslip_old_ids = [(6, 0, payslip_objs.ids)]

    def _assign_old_payslips(self):
        for payslip in self:
            start_date = payslip.date_from.replace(day=1)
            end_date = (start_date + relativedelta(months=1, days=-1))
            
            domain = [
                ('id', '!=', payslip.id),  # Para excluir la nómina actual
                ('employee_id', '=', payslip.employee_id.id),
                ('contract_id', '=', payslip.contract_id.id),
                ('date_from', '>=', start_date.strftime('%Y-%m-%d')),
                ('date_to', '<=', end_date.strftime('%Y-%m-%d')),
                ('struct_id.process', 'in', ['vacaciones', 'prima']),
            ]
            old_payslips = self.env['hr.payslip'].search(domain)
            payslip.payslip_old_ids = [(6, 0, old_payslips.ids)]

    def _compute_extra_hours(self):
        for payslip in self:
            if payslip.struct_id.process in ('nomina', 'contrato', 'otro'):
                query = """
                UPDATE hr_overtime
                SET payslip_run_id = %s
                WHERE 
                    (state = 'validated' OR payslip_run_id IS NULL)
                    AND date_end BETWEEN %s AND %s
                    AND employee_id = %s
                """
                self.env.cr.execute(query, (payslip.id, payslip.date_from, payslip.date_to, payslip.employee_id.id))

    def _compute_novedades(self):
        for payslip in self:
            query_params = [payslip.id, payslip.employee_id.id]
            date_conditions = ""
            if payslip.struct_id.process in ('nomina', 'contrato', 'otro', 'prima'):
                date_conditions = "AND date >= %s AND date <= %s"
                query_params.extend([payslip.date_from, payslip.date_to])

            query = """
            UPDATE hr_novelties_different_concepts
            SET payslip_id = %s
            WHERE payslip_id IS NULL 
            AND employee_id = %s 
            """ + date_conditions
            self.env.cr.execute(query, tuple(query_params))

    def get_worked_day_lines(self):
        res = []
        for rec in self:
            contract = rec.contract_id
            date_from = rec.date_from
            start_period = rec.date_from.replace(day=1)
            date_to = rec.date_to

            # Obtener el último cambio salarial antes de la fecha de inicio del período
            wage_changes_sorted = sorted(contract.change_wage_ids, key=lambda x: x.date_start)
            last_wage_change = max((change for change in wage_changes_sorted if change.date_start < date_from), default=None)
            current_wage_day = last_wage_change.wage / 30 if last_wage_change else contract.wage / 30
            
            leaves_worked_lines = {}
            worked_days = 0
            worked_aux_days = 0
            worked30 = 0
            hp_type = rec.struct_id.process
            annual_parameters = self.env['hr.annual.parameters'].search([('year', '=', date_to.year)])
            w_hours = annual_parameters.hours_daily
            
            # Tipos de entrada de trabajo
            days31 = self.env['hr.work.entry.type'].search([("code", "=", "WORK131")], limit=1)
            outdays = self.env['hr.work.entry.type'].search([("code", "=", "OUT")], limit=1)
            wdays = self.env['hr.work.entry.type'].search([("code", "=", "WORK100")], limit=1)
            wdayst = self.env['hr.work.entry.type'].search([("code", "=", "WORK_D")], limit=1)
            prevdays = self.env['hr.work.entry.type'].search([("code", "=", "PREV_PAYS")], limit=1)
            
            ps_types = ['nomina', 'contrato']
            if not rec.company_id.fragment_vac:
                ps_types.append('Vacaciones')

            # Línea para el total de días del período
            if hp_type in ps_types:
                lab_days = rec.days_between(start_period, date_to)
                res.append({
                    'work_entry_type_id': wdayst.id,
                    'name': 'Total días del período',
                    'sequence': 1,
                    'code': 'TOTAL_DIAS',
                    'symbol': '',
                    'number_of_days': lab_days,
                    'number_of_hours': w_hours * lab_days,
                    'contract_id': contract.id
                })

                # Consulta para obtener días de trabajo anteriores
                query = """
                    SELECT
                        SUM(wd.number_of_days) AS number_of_days,
                        wd.symbol,
                        hw.code
                    FROM hr_payslip_worked_days wd
                    INNER JOIN hr_payslip hp ON hp.id = wd.payslip_id
                    LEFT JOIN hr_work_entry_type hw ON hw.id = wd.work_entry_type_id
                    WHERE hp.date_from >= %s
                        AND hp.date_to <= %s
                        AND hp.contract_id = %s
                        AND hp.id != %s
                        AND hw.code NOT IN ('WORK_D', 'LICENCIA_REMUNERADA')
                        AND hp.struct_process IN ('vacaciones', 'nomina', 'contrato')
                        AND hp.state IN ('done', 'paid')
                    GROUP BY wd.symbol, hw.code
                """
                params = (date_from, date_to, contract.id, rec.id)
                self._cr.execute(query, params)
                wd_other_data = self._cr.fetchall()

                wd_other = 0
                wd_prev = 0
                wd_minus = 0
                
                # Procesar los resultados de la consulta
                for number_of_days, symbol, code in wd_other_data:
                    if code == 'WORK_D':
                        wd_other += number_of_days
                    else:
                        if code in ('PREV_AUS', 'PREV_PAYS'):
                            wd_prev += number_of_days
                        elif symbol in ('-', '') and code not in ('OUT', 'VAC', 'VACDISFRUTADAS'):
                            wd_minus += number_of_days

                sum_wdo = wd_minus - wd_prev
                wd_other = sum_wdo

                # Calcular días trabajados y ausencias
                date_tmp = date_from
                out_of_contract_days = 0
                while date_tmp <= date_to:
                    is_absence_day = any(
                        leave.date_from.date() <= date_tmp <= leave.date_to.date()
                        for leave in rec.leave_ids.leave_id
                    )
                    is_within_contract = contract.date_start <= date_tmp <= (contract.date_end or date_tmp)
                    wage_change_today = next((change for change in wage_changes_sorted if change.date_start == date_tmp), None)
                    if wage_change_today:
                        current_wage_day = wage_change_today.wage / 30

                    if is_within_contract:
                        if is_absence_day:
                            leave = next(leave for leave in rec.leave_ids.leave_id if leave.date_from.date() <= date_tmp <= leave.date_to.date())
                            key = (leave.holiday_status_id.id, '-')
                            
                            absence_line = next((line for line in leave.line_ids.filtered(lambda line: not line.leave_id.holiday_status_id.is_vacation_money)if line.date == date_tmp), None)
                            
                            if absence_line:
                                days_to_subtract = min(absence_line.days_payslip, 1)
                                hour_to_subtract = absence_line.hours
                                amount = absence_line.amount
                                if key not in leaves_worked_lines:
                                    leaves_worked_lines[key] = {
                                        'work_entry_type_id': leave.holiday_status_id.work_entry_type_id.id,
                                        'name': f"Días {leave.holiday_status_id.name.capitalize()}",
                                        'sequence': 5,
                                        'code': leave.holiday_status_id.code or 'nocode',
                                        'symbol': '-',
                                        'amount': amount,
                                        'number_of_days': days_to_subtract,
                                        'number_of_hours': hour_to_subtract,
                                        'contract_id': contract.id,
                                    }
                                else:
                                    leaves_worked_lines[key]['number_of_days'] += days_to_subtract
                                    leaves_worked_lines[key]['number_of_hours'] += hour_to_subtract
                                    leaves_worked_lines[key]['amount'] += amount
                                if leave.holiday_status_id.sub_not_aux:
                                    worked_aux_days +=  days_to_subtract
                        else:
                            # Manejo especial para febrero
                            if date_tmp.month == 2:
                                last_day_of_february = calendar.monthrange(date_tmp.year, 2)[1]
                                if date_tmp.day == last_day_of_february:
                                    if date_tmp.day == 28:
                                        worked_days += 3
                                        worked_aux_days += 3
                                    else:
                                        worked_days += 2
                                        worked_aux_days += 2
                                else:
                                    worked_days += 1
                            # Manejo especial para meses con 31 días
                            elif date_tmp.day == 31:
                                if any(leave.date_from.date() <= date_tmp <= leave.date_to.date() and leave.apply_day_31 for leave in rec.leave_ids.leave_id):
                                    worked_days -= 1
                                    worked_aux_days -= 1
                                    worked30 = 0
                                else:
                                    worked_days += 0
                                    worked_aux_days += 0
                                    worked30 = 1
                            else:
                                worked_days += 1
                                worked_aux_days += 1
                    else:
                        # Días fuera de contrato
                        out_of_contract_days += 1

                    date_tmp += timedelta(days=1)

                # Agregar línea para días fuera de contrato
                if out_of_contract_days > 0:
                    description = 'Deducción por inicio de contrato' if date_tmp < contract.date_start else 'Deducción por fin de contrato'
                    res.append({
                        'work_entry_type_id': outdays.id,
                        'name': description,
                        'sequence': 2,
                        'code': 'OUT',
                        'symbol': '-',
                        'number_of_days': out_of_contract_days,
                        'number_of_hours': w_hours * out_of_contract_days,
                        'contract_id': contract.id,
                    })
                res.extend(leaves_worked_lines.values())
                
                res.append({
                    'work_entry_type_id': wdays.id,
                    'name': 'Días Trabajados',
                    'sequence': 6,
                    'code': 'WORK100',
                    'symbol': '+',
                    'amount': current_wage_day * worked_days,
                    'number_of_days': worked_days,
                    'number_of_hours': worked_days * w_hours,
                    'number_of_days_aux': worked_aux_days,
                   'number_of_hours_aux': worked_aux_days * w_hours,
                    'contract_id': contract.id
                })
                if rec.struct_id.regular_31:
                    res.append({
                        'work_entry_type_id': days31.id,
                        'name': 'Día 31',
                        'sequence': 6,
                        'code': 'WORK131',
                        'symbol': '+',
                        'amount': current_wage_day * worked30,
                        'number_of_days': worked30,
                        'number_of_hours': worked30 * w_hours,
                        'number_of_days_aux': worked30,
                        'number_of_hours_aux': worked30 * w_hours,
                        'contract_id': contract.id
                    })
                if wd_other:
                    res.append({
                        'work_entry_type_id': prevdays.id,
                        'name': 'Días Previos',
                        'sequence': 7,
                        'code': 'PREV_PAYS',
                        'symbol': '-',
                        'number_of_days': wd_other,
                        'number_of_hours': wd_other * w_hours,
                        'contract_id': contract.id
                    })
        return res
    
    def compute_slip(self):
        self_ids = tuple(self._ids)
        if not self_ids:
            return True

        self._cr.execute("""
            SELECT id, struct_id, date_from, date_to, contract_id, employee_id, 
                struct_process, date_liquidacion, pay_primas_in_payroll, 
                pay_cesantias_in_payroll, number
            FROM hr_payslip
            WHERE id IN %s AND state IN ('draft', 'verify')
        """, (self_ids,))
        slips_data = self._cr.dictfetchall()

        if not slips_data:
            return True

        today = fields.Date.today()
        Sequence = self.env['ir.sequence']
        PayslipLine = self.env['hr.payslip.line']
        for slip_data in slips_data:
            slip = self.browse(slip_data['id'])
            slip._update_prima_cesantias_dates(slip, slip_data)
            
            # Check for duplicates
            if slip._check_duplicate_slip(slip_data):
                raise UserError(f"No puede existir más de una nómina del mismo tipo y periodo para el empleado {slip.employee_id.name}")


            name = f"Nomina de {slip.contract_id.name}"
            slip.write({
                'name': name,
                'state': 'verify',
                'compute_date': today
            })

            # Process leave, extra hours, and novedades
            slip.leave_ids.unlink()
            slip.compute_sheet_leave()
            slip._compute_extra_hours()
            slip._process_loan_lines()
            slip._compute_novedades()
            self._cr.execute("DELETE FROM hr_payslip_worked_days WHERE payslip_id = %s", (slip.id,))
            self._cr.execute("DELETE FROM hr_payslip_line WHERE slip_id = %s", (slip.id,))
            self.env.flush_all()
            # Create new worked days and payslip lines
            worked_days_line_ids = slip.get_worked_day_lines()
            slip.worked_days_line_ids = [(0, 0, line) for line in worked_days_line_ids]
            PayslipLine.create(slip._get_payslip_lines_lavish())

        return True


    def recompute_worked_days_action(self):
        errors = []
        success = 0
        for slip in self:
            try:
                slip.leave_ids.unlink()
                slip.worked_days_line_ids.unlink()
                slip.compute_sheet_leave()
                worked_days_line_ids = slip.get_worked_day_lines()
                _logger.error("Error procesando nómina %s", worked_days_line_ids)
                slip.worked_days_line_ids = [(0, 0, line) for line in worked_days_line_ids]
            except Exception as e:
                _logger.error("Error procesando nómina %s: %s", slip.name, str(e))
                errors.append(f'Error en nómina {slip.name}: {str(e)}')
        message = f'Proceso completado.\nNóminas actualizadas: {success}'
        if errors:
            message += '\n\nErrores encontrados:\n' + '\n'.join(errors)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Resultado del recálculo',
                'message': message,
                'sticky': True,
                'type': 'info' if success else 'warning',
            }
        }

    def _update_prima_cesantias_dates(self, slip, slip_data):
        if slip_data['struct_process'] in ['prima', 'contrato'] or slip_data['pay_primas_in_payroll']:
            from_month = 1 if slip_data['date_from'].month <= 6 else 7
            date_from = slip_data['date_from'].replace(month=from_month, day=1)
            if date_from < slip.contract_id.date_start:
                date_from = slip.contract_id.date_start
            slip.date_prima = date_from
        if slip_data['struct_process'] in ['cesantias', 'contrato'] or slip_data['pay_cesantias_in_payroll']:
            date_ref = slip_data['date_to']
            date_from = date_ref.replace(month=1, day=1)
            if date_from < slip.contract_id.date_start:
                date_from = slip.contract_id.date_start
            slip.date_cesantias = date_from

  

    def _check_duplicate_slip(self, slip_data):
        if slip_data['struct_process'] not in ('vacaciones', 'contrato', 'otro'):
            self._cr.execute("""
                SELECT COUNT(id) FROM hr_payslip
                WHERE contract_id = %s AND date_from >= %s AND date_to <= %s
                AND struct_process = %s AND id != %s
            """, (slip_data['contract_id'], slip_data['date_from'], slip_data['date_to'], 
                slip_data['struct_process'], slip_data['id']))
            return self._cr.fetchone()[0] > 0
        return False
    
    def _get_localdict_payslip(self):
        self.ensure_one()
        worked_days_dict = {line.code: line for line in self.worked_days_line_ids if line.code}
        date_from = self.date_from
        start_period = date_from.replace(day=1)
        date_to = self.date_to
        date_from_time = datetime.combine(date_from, datetime.min.time())
        date_to_time = datetime.combine(date_to, datetime.max.time())
        # Check for multiple inputs of the same type and keep a copy of
        # them because otherwise they are lost when building the dict
        input_list = [line.code for line in self.input_line_ids if line.code]
        cnt = Counter(input_list)
        multi_input_lines = [k for k, v in cnt.items() if v > 1]
        same_type_input_lines = {line_code: [line for line in self.input_line_ids if line.code == line_code] for line_code in multi_input_lines}

        inputs_dict = {line.code: line for line in self.input_line_ids if line.code}
        employee = self.employee_id
        contract = self.contract_id
        wage = False
        annual_parameters = self.env['hr.annual.parameters'].search([('year', '=', date_to.year)])
        pslp_query = """
            SELECT hp.id
            FROM hr_payslip AS hp
            WHERE hp.contract_id = %s
                AND hp.date_from >= %s
                AND hp.date_to <= %s
                AND hp.id != %s
                AND hp.state in ('done','paid')
        """
        params = (contract.id, start_period, date_to, self.id)
        self._cr.execute(pslp_query, params)
        payslip_ids = [row[0] for row in self._cr.fetchall()]
        payslips_month = self.env['hr.payslip'].browse(payslip_ids) if payslip_ids else self.env['hr.payslip'].browse()
        if not annual_parameters:
            raise UserError('Falta Configurar los parametros anuales ir a --> Configuracion/ Parametros anuales')
        wage = contract.wage
        obj_wage = self.env['hr.contract.change.wage'].search([('contract_id', '=', contract.id), ('date_start', '<', date_to)])
        for change in sorted(obj_wage, key=lambda x: x.date_start):
            if float(change.wage) > 0:
                wage = change.wage 
               
        #if wage <= 0:
        #    raise UserError('El sueldo no puede ser igual a 0 o menor')
        localdict = {
            **self._get_base_local_dict(),
            **{
                'categories': BrowsableObject(employee.id, {}, self.env),
                'rules_computed': BrowsableObject(employee.id, {}, self.env),
                'rules': BrowsableObject(employee.id, {}, self.env),
                'payslip': Payslips(employee.id, self, self.env),
                'worked_days': WorkedDays(employee.id, worked_days_dict, self.env),
                'inputs': InputLine(employee.id, inputs_dict, self.env),
                'employee': employee,
                'contract': contract,
                'result_rules': ResultRules(employee.id, {}, self.env),
                'result_rules_co': ResultRules_co(employee.id, {}, self.env),
                'same_type_input_lines': same_type_input_lines,
                'wage':wage,
                'slip': self,
                'id_contract_concepts':0,
                'annual_parameters': annual_parameters,
                'date_to_time':date_to_time,
                'date_from_time':date_from_time,
                'payslips_month':payslips_month,
                'inherit_contrato':0,
            }
        }
        return localdict



    def _sum_salary_rule_category(self, localdict, category, amount):
        if category.parent_id:
            localdict = self._sum_salary_rule_category(localdict, category.parent_id, amount)
        localdict['categories'].dict[category.code] = localdict['categories'].dict.get(category.code, 0) + amount
        return localdict

    def _sum_salary_rule(self, localdict, rule, amount):
        localdict['rules_computed'].dict[rule.code] = localdict['rules_computed'].dict.get(rule.code, 0) + amount
        return localdict



    def _get_payslip_lines_lavish(self):
        for payslip in self:
            if not payslip.contract_id:
                raise UserError(_("No hay ningún contrato establecido en el recibo de sueldo %s para %s. Verifique que haya al menos un contrato establecido en el formulario del empleado.", payslip.name, payslip.employee_id.name))
            
            localdict = self.env.context.get('force_payslip_localdict', None) or payslip._get_localdict_payslip()
            # Inicializar los diccionarios si no existen
            localdict['rules'] = localdict.get('rules', {})
            localdict['result_rules'] = localdict.get('result_rules', {})
            localdict['result_rules_co'] = localdict.get('result_rules_co', {})
            # Calcular ausencias y actualizar diccionario
            result_leave = {}
            result = {}
            absence_dict = payslip._calculate_absences()
            localdict, result_leave = payslip._update_localdict_for_absences(localdict, absence_dict)
            localdict, result = payslip._calculate_absences_and_update_dict(payslip, localdict)
            result = payslip._process_salary_rules(payslip, localdict, result)
            combined_result = {**result_leave, **result}
            return list(combined_result.values())

    def _calculate_absences_and_update_dict(self, payslip, localdict):
        """
        Procesa conceptos y actualiza el diccionario local
        """
        result = {}
        for concept in localdict['contract'].concepts_ids.filtered(lambda l: l.state == 'done'):
            if not self._should_process_concept(concept, payslip):
                continue
            amount = concept._calculate_period_amount_slip(payslip.date_from, payslip.date_to)
            if amount == 0:
                continue
            payments = self._get_concept_payments(concept, payslip, amount)
            for payment in payments:
                localdict, result = self._create_concept_line(
                    localdict, 
                    concept, 
                    payment['amount'],
                    payment['is_previous'],
                    payment['description'],
                    result
                )
                
        if payslip.loan_installment_ids:
            for installment in payslip.loan_installment_ids:
                localdict, result = self._create_loan_line(
                    localdict,
                    installment,
                    result
                )

        obj_novelties = self.env['hr.novelties.different.concepts'].search([
            ('employee_id', '=', localdict['employee'].id),
            ('payslip_id', '=', payslip.id),
            ('date', '>=', localdict['slip'].date_from),
            ('date', '<=', localdict['slip'].date_to)
        ])

        for concepts in obj_novelties:
            if concepts.amount != 0 and self._should_process_novelty(concepts, payslip):
                localdict, result = self._update_localdict_for_novelty(localdict, concepts, result)

        return localdict, result

    def _should_process_novelty(self, novelty, payslip):
        """
        Determina si una novedad debe ser procesada según estructuras y condiciones
        """
        if not novelty.salary_structure_ids:
            return payslip.struct_process in ['nomina', 'contrato']
        
        return payslip.struct_id.id in novelty.salary_structure_ids.ids

    def _process_salary_rules(self, payslip, localdict, result):
        rules_to_process = self._get_rules_to_process()
        blacklisted_rule_ids = self.env.context.get('prevent_payslip_computation_line_ids', [])
        rule_results = {}
        overrides = {}
        if payslip.has_overrides or self.env.context.get('simulate_override'):
            if self.env.context.get('simulate_override'):
                overrides = {
                    self.env.context.get('override_rule'): {
                        'type': self.env.context.get('override_type'),
                        'value': self.env.context.get('override_value')
                    }
                }
            else:
                overrides = {
                    o.rule_id.code: {
                        'type': o.override_type,
                        'value': o.value_override
                    } for o in payslip.rule_override_ids.filtered('active')
                }
        _logger.error(overrides)
        for rule in sorted(rules_to_process, key=lambda x: x.sequence):
            if rule.id in blacklisted_rule_ids:
                continue
                
            temp_dict = localdict.copy()
            if rule._satisfy_condition(temp_dict):
                amount, qty, rate, name, log, data = rule._compute_rule_lavish(temp_dict)
                tot_rule = 0 
                # Aplicar override si existe para esta regla
                if rule.code in overrides:
                    override = overrides[rule.code]
                    if override['type'] == 'amount':
                        amount = override['value']
                    elif override['type'] == 'quantity':
                        qty = override['value']
                    elif override['type'] == 'rate':
                        rate = override['value']
                    elif override['type'] == 'total':
                        tot_rule = override['value']
                   
                if not tot_rule:
                    tot_rule = round(amount * qty * rate / 100.0)

                previous_amount = rule.code in temp_dict and temp_dict[rule.code] or 0.0
                
                temp_dict['result_rules_co'].dict[rule.code] = {
                    'total': tot_rule, 
                    'amount': tot_rule, 
                    'quantity': 1,
                    'base_seguridad_social': rule.base_seguridad_social, 
                    'base_prima': rule.base_prima,
                    'base_cesantias': rule.base_cesantias, 
                    'base_vacaciones': rule.base_vacaciones,
                    'base_vacaciones_dinero': rule.base_vacaciones_dinero
                }
                
                temp_dict = self._sum_salary_rule_category(temp_dict, rule.category_id, tot_rule - previous_amount)
                temp_dict = self._sum_salary_rule(temp_dict, rule, tot_rule)

                if tot_rule != 0.0:
                    rule_results[rule.code] = self._prepare_rule_result(rule, temp_dict, amount, qty, rate, name, log, payslip, data)

        result.update(rule_results)
        return result

    def _should_process_concept(self, concept, payslip):
        """
        Valida si un concepto debe ser procesado
        """
        if not concept.active_period or concept.state != 'done':
            return False

        if not self._check_structure_compatibility(concept, payslip):
            return False

        return True

    def _check_structure_compatibility(self, concept, payslip):
        """
        Verifica si el concepto es compatible con la estructura salarial de la nómina
        """
        if not concept.payroll_structure_ids:
            return payslip.struct_process in ['nomina', 'contrato']
            
        return payslip.struct_id.id in concept.payroll_structure_ids.ids


    def _get_concept_payments(self, concept, payslip, amount):
        """
        Determina los pagos a realizar para el concepto
        """
        payments = []
        
        is_double = False
        if concept.force_double_payment and concept.double_payment_date:
            if payslip.date_from <= concept.double_payment_date <= payslip.date_to:
                is_double = True
                
                payments.append({
                    'amount': amount,
                    'description': f"{concept.description or concept.input_id.name} (Período Anterior)",
                    'is_previous': True
                })
                
                concept._mark_double_payment_applied()
        skip = concept._get_active_skip(payslip.date_from, payslip.date_to)
        if skip:
            # if skip.period_double:
            #     is_double = True
            #     payments.append({
            #         'amount': amount,
            #         'description': f"{concept.description or concept.input_id.name} (Recuperación)",
            #         'is_previous': True
            #     })
            #     skip.action_apply()
            # else:
            return [] 

        if concept.aplicar == '15' and payslip.date_to.day > 15:
            return []
        elif concept.aplicar == '30' and payslip.date_from.day <= 15:
            return []

        payments.append({
            'amount': amount,
            'description': concept.description or concept.detail or  concept.input_id.name,
            'is_previous': False
        })

        return payments



    def _create_concept_line(self, localdict, concept, amount, is_previous, description, result):
        """
        Crea una línea de concepto y actualiza el localdict
        """
        line_code = concept.input_id.code + '-PCD' + str(concept.id)
        if is_previous:
            line_code += '-PREV'
        previous_amount = concept.input_id.code in localdict and localdict[concept.input_id.code] or 0.0
        
        localdict[line_code] = amount
        localdict['rules'].dict[line_code] = concept.input_id
        
        rule = concept.input_id
        rule_values = {
            'total': amount,
            'amount': amount,
            'quantity': 1,
            'base_prima': rule.base_prima,
            'base_cesantias': rule.base_cesantias,
            'base_vacaciones': rule.base_vacaciones,
            'base_vacaciones_dinero': rule.base_vacaciones_dinero
        }
        
        localdict['result_rules'].dict[line_code] = rule_values
        localdict['result_rules_co'].dict[line_code] = {
            **rule_values,
            'base_seguridad_social': rule.base_seguridad_social
        }
        
        localdict = self._sum_salary_rule_category(
            localdict, 
            concept.input_id.category_id, 
            amount - previous_amount
        )
        localdict = self._sum_salary_rule(localdict, concept.input_id, amount)
        
        result[line_code] = {
            'sequence': concept.input_id.sequence,
            'code': concept.input_id.code,
            'name': description,
            'salary_rule_id': concept.input_id.id,
            'contract_id': localdict['contract'].id,
            'employee_id': localdict['employee'].id,
            'entity_id': concept.partner_id.id,
            'loan_id': concept.loan_id.id,
            'concept_id': concept.id,
            'amount': amount,
            'quantity': 1.00,
            'rate': 100,
            'total': amount,
            'slip_id': self.id,
            'is_previous_period': is_previous
        }
        
        return localdict, result
    
    def _create_loan_line(self, localdict, installment, result):
        """
        Crea una línea para una cuota de préstamo
        """
        line_code = f'LOAN-{installment.loan_id.id}-{installment.sequence}'
        
        loan = installment.loan_id
        amount = -abs(installment.amount)  # Monto negativo para descuento
        
        description = f"Cuota {installment.sequence}/{len(loan.installment_ids)} -{[loan.category_id.code]} {loan.category_id.name}"
        if len(localdict['slip'].loan_installment_ids) > 1:
            description += f" ({installment.date})"
        
        # Obtener la regla salarial para préstamos
        rule = installment.loan_id.category_id.salary_rule_id#self.env.ref('hr_loan.rule_loan_payment', raise_if_not_found=False)
        if not rule:
            return localdict, result
        
        localdict[line_code] = amount
        localdict['rules'].dict[line_code] = rule
        
        rule_values = {
            'total': amount,
            'amount': amount,
            'quantity': 1,
            'base_prima': False,
            'base_cesantias': False,
            'base_vacaciones': False,
            'base_vacaciones_dinero': False
        }
        
        localdict['result_rules'].dict[line_code] = rule_values
        localdict['result_rules_co'].dict[line_code] = {
            **rule_values,
            'base_seguridad_social': False
        }
        
        localdict = self._sum_salary_rule_category(
            localdict, 
            rule.category_id,
            amount
        )
        localdict = self._sum_salary_rule(localdict, rule, amount)
        
        result[line_code] = {
            'sequence': rule.sequence,
            'code': rule.code,
            'name': description,
            'salary_rule_id': rule.id,
            'contract_id': localdict['contract'].id,
            'employee_id': localdict['employee'].id,
            'entity_id': loan.entity_id.id,
            'loan_id': loan.id,
            #'loan_installment_id': installment.id,
            'amount': amount,
            'quantity': 1.00,
            'rate': 100,
            'total': amount,
            'slip_id': self.id,
            'is_previous_period': False
        }
        
        return localdict, result
    
    def _update_localdict_for_novelty(self, localdict, concepts, result):
        previous_amount = concepts.salary_rule_id.code in localdict and localdict[concepts.salary_rule_id.code] or 0.0
        tot_rule = self._get_payslip_line_total(concepts.amount, 1, 100, concepts.salary_rule_id)
        localdict[concepts.salary_rule_id.code+'-PCD'] = tot_rule
        localdict['rules'].dict[concepts.salary_rule_id.code+'-PCD'] = concepts.salary_rule_id
        localdict = self._sum_salary_rule_category(localdict, concepts.salary_rule_id.category_id, tot_rule - previous_amount)
        localdict = self._sum_salary_rule(localdict, concepts.salary_rule_id, tot_rule)
        rule = concepts.salary_rule_id
        localdict['result_rules'].dict[rule.code +'-PCD'+str(concepts.id)] = {
            'total': tot_rule, 'amount': tot_rule, 'quantity': 1, 
            'base_prima':rule.base_prima, 'base_cesantias':rule.base_cesantias, 
            'base_vacaciones':rule.base_vacaciones,'base_vacaciones_dinero':rule.base_vacaciones_dinero
        }
        localdict['result_rules_co'].dict[rule.code +'-PCD'+str(concepts.id)] = {
            'total': tot_rule, 'amount': tot_rule, 'quantity': 1, 
            'base_seguridad_social': rule.base_seguridad_social, 'base_prima':rule.base_prima, 
            'base_cesantias':rule.base_cesantias, 'base_vacaciones':rule.base_vacaciones,
            'base_vacaciones_dinero':rule.base_vacaciones_dinero
        }

        result_item = concepts.salary_rule_id.code+'-PCD'+str(concepts.id)
        result[result_item] = {
            'sequence': concepts.salary_rule_id.sequence,
            'code': concepts.salary_rule_id.code,
            'name': concepts.description or concepts.salary_rule_id.name,
            #'note': concepts.salary_rule_id.note,
            'salary_rule_id': concepts.salary_rule_id.id,
            'contract_id': localdict['contract'].id,
            'employee_id': localdict['employee'].id,
            'entity_id': concepts.partner_id.id if concepts.partner_id else False,
            'amount': tot_rule,
            'quantity': 1.0,
            'rate': 100,
            'total': tot_rule,
            'slip_id': self.id,
        }
        return localdict, result
    
    def _prepare_rule_result(self, rule, localdict, amount, qty, rate, name, log, payslip, data):
        tot_rule = payslip._get_payslip_line_total(amount, qty, rate, rule)
        result = {
            'sequence': rule.sequence,
            'code': rule.code,
            'name':  name or rule.name,
            'salary_rule_id': rule.id,
            'contract_id': localdict['contract'].id,
            'employee_id': localdict['employee'].id,
            'entity_id': False,
            'amount': amount,
            'quantity': qty,
            'rate': rate,
            'total': tot_rule,
            'slip_id': payslip.id,
            'run_id': payslip.payslip_run_id.id,
        }
        
        if rule.category_id.code == 'SSOCIAL':
            for entity in localdict['employee'].social_security_entities:
                if entity.contrib_id.type_entities == 'eps' and rule.code == 'SSOCIAL001':
                    result['entity_id'] = entity.partner_id.id
                elif entity.contrib_id.type_entities == 'pension' and rule.code in ['SSOCIAL002', 'SSOCIAL003', 'SSOCIAL004']:
                    result['entity_id'] = entity.partner_id.id
                elif entity.contrib_id.type_entities == 'subsistencia' and rule.code == 'SSOCIAL003':
                    result['entity_id'] = entity.partner_id.id
                elif entity.contrib_id.type_entities == 'solidaridad' and rule.code == 'SSOCIAL004':
                    result['entity_id'] = entity.partner_id.id

        if rule.code in ("PRIMA", "CESANTIAS", "INTCESANTIAS") or rule.category_id.code == 'PROV':
            if data:
                result.update({
                    'days_unpaid_absences': data['susp'],                        
                    'amount_base': data['base_periodo'],
                    'initial_accrual_date': data['date_from'],
                    'final_accrual_date':  data['date_to'],
                    'computation': json.dumps(data['data_kpi'], default=json_serial),
                }) 
        elif rule.code in ('RT_MET_01',):
            result.update({
                'computation': json.dumps(log, default=json_serial),                                          
            }) 
            self.resulados_rt = self._format_reporte_html(log)
        elif rule.code in ('IBD'):
            result.update({
                'computation': json.dumps(log, default=json_serial),                                          
            }) 
            self.resulados_op = self.generate_ibd_html_report(log)
        return result

    def _calculate_absences(self):
        self.ensure_one()
        temp_dict = {}
        for leave_day in self.leave_days_ids:
            composite_key = (leave_day.leave_id.id, leave_day.rule_id.id)
            if composite_key not in temp_dict:
                temp_dict[composite_key] = {
                    'name': leave_day.leave_id.name,
                    'total_days': 0,
                    'total_amount': 0,
                    'leave_type': leave_day.leave_id.holiday_status_id.name,
                    'date_from': leave_day.date,
                    'date_to': leave_day.date,
                    'rule_id': leave_day.rule_id,
                    'leave_id': leave_day.leave_id,
                    'entity_id': leave_day.leave_id.entity.id if leave_day.leave_id.entity else False,
                    'days_work': 0,
                    'days_holiday': 0,
                    'days_31': 0,
                    'days_holiday_31': 0,
                    'additional_novelties': [],
                }
            else:
                temp_dict[composite_key]['date_from'] = min(temp_dict[composite_key]['date_from'], leave_day.date)
                temp_dict[composite_key]['date_to'] = max(temp_dict[composite_key]['date_to'], leave_day.date)
                
            # Acumular los días y montos
            temp_dict[composite_key]['total_days'] += leave_day.days_payslip
            temp_dict[composite_key]['total_amount'] += leave_day.amount
            temp_dict[composite_key]['days_work'] += leave_day.days_work
            temp_dict[composite_key]['days_holiday'] += leave_day.days_holiday
            temp_dict[composite_key]['days_31'] += leave_day.days_31
            temp_dict[composite_key]['days_holiday_31'] += leave_day.days_holiday_31
            
            # Agregar novedad individual
            temp_dict[composite_key]['additional_novelties'].append({
                'date': leave_day.date,
                'amount': leave_day.amount,
                'days': leave_day.days_payslip,
                'days_work': leave_day.days_work,
                'days_holiday': leave_day.days_holiday,
                'days_31': leave_day.days_31,
                'days_holiday_31': leave_day.days_holiday_31,
            })
        
        # Construir el diccionario final con los totales
        absence_dict = {}
        for (leave_id, rule_id), data in temp_dict.items():
            # Create a composite key for the final dictionary
            composite_key = f"{leave_id}_{rule_id}"
            absence_dict[composite_key] = {
                'name': data['name'],
                'total_days': data['total_days'],
                'total_amount': data['total_amount'],
                'leave_type': data['leave_type'],
                'date_from': data['date_from'],
                'date_to': data['date_to'],
                'rule_id': data['rule_id'],
                'leave_id': data['leave_id'],
                'entity_id': data['entity_id'],
                'days_work': data['days_work'],
                'days_holiday': data['days_holiday'],
                'days_31': data['days_31'],
                'days_holiday_31': data['days_holiday_31'],
            }
            
            # Ordenar las novedades por fecha
            data['additional_novelties'].sort(key=lambda x: x['date'])
            absence_dict[composite_key]['additional_novelties'] = data['additional_novelties']
        return absence_dict


    def _update_localdict_for_absences(self, localdict, absence_dict):
        result = {}
        for leave_id, absence_data in absence_dict.items():
            if not absence_data['rule_id']:
                continue
    
            concept = {
                'input_id': absence_data['rule_id'],
                'leave_id': absence_data['leave_id'],
                'partner_id': absence_data['entity_id'],
                'loan_id': False,
                'days': absence_data['total_days'],
                'days_work': absence_data['days_work'],
                'days_holiday': absence_data['days_holiday'],
                'days_31': absence_data['days_31'],
                'days_holiday_31': absence_data['days_holiday_31'],
                'leave_type': absence_data['leave_type'],
                'date_from': absence_data['date_from'],
                'date_to': absence_data['date_to'],
            }
            tot_rule = absence_data['total_amount']
            
            localdict, result = self._update_localdict_for_leave(localdict, concept, tot_rule, result)
            
        return localdict, result
    
    def _update_localdict_for_leave(self, localdict, concept, tot_rule, result):
        if not self.settle_payroll_concepts or self.struct_process in ['prima', 'cesantias', 'intereses_cesantias']:
            return localdict, result
        input_code = concept['input_id'].code
        previous_amount = localdict.get(input_code, 0.0)
        localdict[f"{input_code}-PCD{concept['leave_id']}"] = tot_rule
        localdict['rules'].dict[f"{input_code}-PCD{concept['leave_id']}"] = concept['input_id']
        rule = concept['input_id']
        
        days = concept['days']
        amount_per_day = tot_rule / days if days else 0
        
        result_rule = {
            'total': tot_rule,
            'amount': amount_per_day,
            'quantity': days,
            'base_prima': rule.base_prima,
            'base_cesantias': rule.base_cesantias,
            'base_vacaciones': rule.base_vacaciones,
            'base_vacaciones_dinero': rule.base_vacaciones_dinero
        }
        localdict['result_rules'].dict[f"{rule.code}-PCD{concept['leave_id']}"] = result_rule
        
        result_rule_co = result_rule.copy()
        result_rule_co['base_seguridad_social'] = rule.base_seguridad_social
        localdict['result_rules_co'].dict[f"{rule.code}-PCD{concept['leave_id']}"] = result_rule_co
        
        localdict = self._sum_salary_rule_category(localdict, rule.category_id, tot_rule - previous_amount)
        localdict = self._sum_salary_rule(localdict, rule, tot_rule)
        result_item = f"{input_code}-PCD{concept['leave_id']}"
        result[result_item] = {
            'sequence': rule.sequence,
            'code': rule.code,
            'name': rule.name,
            'salary_rule_id': rule.id,
            'contract_id': localdict['contract'].id,
            'employee_id': localdict['employee'].id,
            'entity_id': concept['partner_id'],
            'loan_id': concept['loan_id'],
            'amount': amount_per_day,
            'quantity': days,
            'rate': 100,
            'total': tot_rule,
            'slip_id': self.id,
            'leave_id': concept['leave_id'].id,
            'initial_accrual_date': concept['date_from'],
            'final_accrual_date': concept['date_to'],
            'business_units': concept['days_work'],
            'holiday_units': concept['days_holiday'],
            'business_31_units': concept['days_31'],
            'holiday_31_units': concept['days_holiday_31'],
        }
        
        return localdict, result


    def _get_rules_to_process(self):
        self.ensure_one()
        process = self.struct_id.process
        def get_specific_rules(process):
            return self.env['hr.salary.rule'].search([
                ('struct_id.process', '=', process),
                ('active', '=', True)
            ])
            
        common_rules = self.env['hr.salary.rule'].search([
            ('code', 'in', ['TOTALDEV', 'TOTALDED', 'NET']),
            ('active', '=', True)
        ])
        if process == 'nomina':
            rules = get_specific_rules('nomina')
            if self.pay_primas_in_payroll:
                rules |= get_specific_rules('prima')
            if self.pay_cesantias_in_payroll:
                rules |= self.env['hr.salary.rule'].search([('code','=','INTCES_YEAR')])
            if self.pay_vacations_in_payroll:
                rules |= self.env['hr.salary.rule'].search([('code','in',('VACDISFRUTADAS','VAC001','VAC002'))])
        elif process == 'vacaciones':
            rules = self.env['hr.salary.rule'].search([('code', 'in', ['VACDISFRUTADAS','VACATIONS_MONEY','SSOCIAL001','SSOCIAL002','VAC001','VAC002','IBD','IBC_R', 'TOTALDEV', 'TOTALDED', 'NET'])])
        elif process in ['prima', 'cesantias', 'intereses_cesantias']:
            rules = get_specific_rules(process)
        elif process == 'contrato':
            rules = get_specific_rules('nomina') | get_specific_rules('prima') | \
                    get_specific_rules('cesantias') | get_specific_rules('intereses_cesantias') | \
                    get_specific_rules('vacaciones') 
            if self.have_compensation:
                rules |= self.struct_id.rule_ids            
            if not self.settle_payroll_concepts:
                rules = rules.filtered(lambda r: r.struct_id.process != 'nomina')
            if self.no_days_worked:
                rules = rules.filtered(lambda r: r.category_id.code not in ('BASIC','AUX'))
            if not self.novelties_payroll_concepts:
                rules = rules.filtered(lambda r: r.type_concepts != 'novedad')
        else: 
            rules = self.struct_id.rule_ids
        return rules | common_rules

    def _no_round(self, amount):
        return amount

    def _round1(self, amount):
        return round(amount)

    def _round100(self, amount):
        return int(math.ceil(amount / 100.0)) * 100

    def _round1000(self, amount):
        return round(amount, -3)

    def _round2d(self, amount):
        return round(amount, 2)

    @api.depends('line_ids')
    def _compute_concepts_category(self):
        category_mapping = {
            'EARNINGS': ['BASIC', 'AUX', 'AUS', 'ALW', 'ACCIDENTE_TRABAJO', 'DEV_NO_SALARIAL', 'DEV_SALARIAL', 'TOTALDEV', 'HEYREC', 'COMISIONES', 'INCAPACIDAD', 'LICENCIA_MATERNIDAD', 'LICENCIA_NO_REMUNERADA', 'LICENCIA_REMUNERADA', 'PRESTACIONES_SOCIALES', 'PRIMA', 'VACACIONES'],
            'DEDUCTIONS': ['DED', 'DEDUCCIONES', 'TOTALDED', 'SANCIONES', 'DESCUENTO_AFC', 'SSOCIAL'],
            'PROVISIONS': ['PROV'],
            'OUTCOME': ['NET']}
        categorized_lines = {
            'EARNINGS': [],
            'DEDUCTIONS': [],
            'PROVISIONS': [],
            'BASES': [],
            'OUTCOME': []}
        for payslip_line in self.line_ids:
            category_found = False
            for category, codes in category_mapping.items():
                if payslip_line.category_id.code in codes or payslip_line.category_id.parent_id.code in codes:
                    categorized_lines[category].append(payslip_line.id)
                    category_found = True
                    break
            if not category_found:
                categorized_lines['BASES'].append(payslip_line.id)
        for category, line_ids in categorized_lines.items():
            setattr(self, f'{category.lower()}_ids', self.env['hr.payslip.line'].browse(line_ids))
    
    def compute_sheet_leave(self):
        for rec in self:
            rec.leave_ids.unlink()
            rec.payslip_day_ids.unlink()

            date_from = datetime.combine(rec.date_from, datetime.min.time())
            date_to = datetime.combine(rec.date_to, datetime.max.time())
            employee_id = rec.employee_id.id

            work_entries = self.env['hr.leave'].search([
                ('state', '=', 'validate'),
                ('date_to', '>=', date_from),
                ('date_from', '<=', date_to),
                ('employee_id', '=', employee_id),
            ])

            leave_vals = [{
                'leave_id': leave.id,
                'leave_type': leave.holiday_status_id.name,
                'employee_id': employee_id,
                'total_days': leave.number_of_days,
                'payroll_id': rec.id,
            } for leave in work_entries]

            if leave_vals:
                leave_records = self.env['hr.absence.days'].create(leave_vals)
                all_lines = leave_records.mapped('leave_id.line_ids').filtered(
                    lambda l: l.state == 'validated'
                )

                if rec.struct_id.process == 'vacaciones' or rec.pay_vacations_in_payroll:
                    vacation_lines = all_lines.filtered(lambda l: l.leave_id.holiday_status_id.is_vacation)
                    if vacation_lines:
                        money_lines = vacation_lines.filtered(
                            lambda l: l.leave_id.holiday_status_id.is_vacation_money
                        )
                        time_lines = vacation_lines - money_lines

                        relevant_lines = money_lines

                        if rec.company_id.fragment_vac:
                            relevant_lines |= time_lines.filtered(
                                lambda l: rec.date_from <= l.date <= rec.date_to
                            )
                        else:
                            relevant_lines |= time_lines

                        relevant_lines.write({
                            'payslip_id': rec.id,
                        })

                    other_lines = all_lines - vacation_lines
                    if other_lines:
                        other_lines.filtered(
                            lambda l: rec.date_from <= l.date <= rec.date_to
                        ).write({
                            'payslip_id': rec.id
                        })
                else:
                    relevant_lines = all_lines.filtered(
                        lambda l: (
                            rec.date_from <= l.date <= rec.date_to and
                            not l.leave_id.holiday_status_id.is_vacation and not l.leave_id.holiday_status_id.is_vacation_money
                        )
                    )
                    if relevant_lines:
                        relevant_lines.write({
                            'payslip_id': rec.id
                        })
            rec.compute_worked_days()

    def _get_payslip_line_total(self, amount, quantity, rate, rule):
        self.ensure_one()
        total = amount * quantity * rate / 100.0
        return round(total) 

    def compute_worked_days(self):
        for rec in self:
            payslip_day_ids = []
            wage_changes_sorted = sorted(rec.contract_id.change_wage_ids, key=lambda x: x.date_start)
            last_wage_change_before_payslip = max((change for change in wage_changes_sorted if change.date_start < rec.date_from), default=None)
            current_wage_day = last_wage_change_before_payslip.wage / 30 if last_wage_change_before_payslip else rec.contract_id.wage / 30
            date_tmp = rec.date_from
            while date_tmp <= rec.date_to:
                is_absence_day = any(leave.date_from.date() <= date_tmp <= leave.date_to.date() for leave in rec.leave_ids.leave_id)
                is_within_contract = rec.contract_id.date_start <= date_tmp <= (rec.contract_id.date_end or date_tmp)
                wage_change_today = next((change for change in wage_changes_sorted if change.date_start == date_tmp), None)
                if wage_change_today:
                    current_wage_day = wage_change_today.wage / 30
                if is_within_contract:
                    day_type = 'A' if is_absence_day else 'W'
                    payslip_day_data = {'payslip_id': rec.id, 'day': date_tmp.day, 'day_type': day_type}
                    if not is_absence_day:
                        payslip_day_data['subtotal'] = current_wage_day
                    payslip_day_ids.append(payslip_day_data)
                else:
                    payslip_day_ids.append({'payslip_id': rec.id, 'day': date_tmp.day, 'day_type': 'X'})
                date_tmp += timedelta(days=1)
            # Create payslip day records in bulk
            rec.payslip_day_ids.create(payslip_day_ids)
        return True


    def name_get(self):
        result = []
        for record in self:
            if record.payslip_run_id:
                result.append((record.id, "{} - {}".format(record.payslip_run_id.name,record.employee_id.name)))
            else:
                result.append((record.id, "{} - {} - {}".format(record.struct_id.name,record.employee_id.name,str(record.date_from))))
        return result

    def get_hr_payslip_reports_template(self):
        type_report = self.struct_process if self.struct_process != 'otro' else 'nomina'
        obj = self.env['hr.payslip.reports.template'].search([('company_id','=',self.employee_id.company_id.id),('type_report','=',type_report)])
        if len(obj) == 0:
            raise ValidationError(_('No tiene configurada plantilla de liquidacion. Por favor verifique!'))
        return obj

    def get_pay_vacations_in_payroll(self):
        return bool(self.env['ir.config_parameter'].sudo().get_param('lavish_hr_payroll.pay_vacations_in_payroll')) or False

    def get_increase(self):
        return True

    @api.onchange('employee_id', 'struct_id', 'contract_id', 'date_from', 'date_to')
    def _onchange_employee(self):
        if (not self.employee_id) or (not self.date_from) or (not self.date_to):
            return

        employee = self.employee_id
        date_from = self.date_from
        date_to = self.date_to

        self.company_id = employee.company_id
        if not self.contract_id or self.employee_id != self.contract_id.employee_id:  # Add a default contract if not already defined
            contracts = employee._get_contracts(date_from, date_to)

            if not contracts or not contracts[0].structure_type_id.default_struct_id:
                self.contract_id = False
                self.struct_id = False
                return
            self.contract_id = contracts[0]
            self.struct_id = contracts[0].structure_type_id.default_struct_id

        payslip_name = self.struct_id.payslip_name or _('Recibo de Salario')

        mes = self.date_from.month
        month_name = self.env['hr.birthday.list'].get_name_month(mes)

        date_name = month_name + ' ' + str(self.date_from.year)
        self.name = '%s - %s - %s' % (payslip_name, self.employee_id.name or '', date_name)
        self.analytic_account_id = self.contract_id.analytic_account_id

        if date_to > date_utils.end_of(fields.Date.today(), 'month'):
            self.warning_message = _("This payslip can be erroneous! Work entries may not be generated for the period from %s to %s." %
                (date_utils.add(date_utils.end_of(fields.Date.today(), 'month'), days=1), date_to))
        else:
            self.warning_message = False

    def compute_sheet(self):
        for payslip in self.filtered(lambda slip: slip.state not in ['cancel', 'done','paid']):
            payslip.compute_slip()

    def action_payslip_draft(self):
        for payslip in self:
            payslip.payslip_day_ids.unlink()
            for line in payslip.input_line_ids:
                if line.loan_line_id:
                    line.loan_line_id.paid = False
                    line.loan_line_id.payslip_id = False
                    line.loan_line_id.loan_id._compute_loan_amount()
            payslip.leave_ids.leave_id.line_ids.filtered(lambda l: l.date <= payslip.date_to).write({'payslip_id': False})
        return self.write({'state': 'draft'})

    def restart_payroll(self):
        for payslip in self:
            for line in payslip.input_line_ids:
                if line.loan_line_id:
                    line.loan_line_id.paid = False
                    line.loan_line_id.payslip_id = False
                    line.loan_line_id.loan_id._compute_loan_amount()
            payslip.leave_ids.leave_id.line_ids.filtered(lambda l: l.date <= payslip.date_to).write({'payslip_id': False})
            payslip.mapped('move_id').unlink()
            obj_payslip_line = self.env['hr.payslip.line'].search(
                [('slip_id', '=', payslip.id), ('loan_id', '!=', False)])
            for payslip_line in obj_payslip_line:
                obj_loan_line = self.env['hr.loan.installment'].search(
                    [('employee_id', '=', payslip_line.employee_id.id), ('prestamo_id', '=', payslip_line.loan_id.id),
                     ('payslip_id', '>=', payslip.id)])
                if payslip.struct_id.process == 'contrato' and payslip_line.loan_id.final_settlement_contract == True:
                    obj_loan_line.unlink()
                else:
                    obj_loan_line.write({
                        'paid': False,
                        'payslip_id': False
                    })
                obj_loan = self.env['hr.loan'].search(
                    [('employee_id', '=', payslip_line.employee_id.id), ('id', '=', payslip_line.loan_id.id)])
                if obj_loan.pending_amount > 0:
                    self.env['hr.contract.concepts'].search([('loan_id', '=', payslip_line.loan_id.id)]).write(
                        {'state': 'done'})
            payslip.line_ids.unlink()
            payslip.not_line_ids.unlink()
            #Eliminar historicos            
            self.env['hr.vacation'].search([('payslip', '=', payslip.id)]).unlink()
            self.env['hr.history.prima'].search([('payslip', '=', payslip.id)]).unlink()
            self.env['hr.history.cesantias'].search([('payslip', '=', payslip.id)]).unlink()
            #Reversar Liquidación            
            payslip.action_payslip_draft()            

    #--------------------------------------------------LIQUIDACIÓN DE LA NÓMINA PERIÓDICA---------------------------------------------------------#
    def calculate_business_and_holidays_between_dates(self, contract, date_from, date_to):
        """
        Calculate business days and holidays between two specific dates using Odoo's holiday model
        
        Args:
        contract: Employee contract record
        date_from: starting date (datetime)
        date_to: ending date (datetime)
        
        Returns:
        dict with business_days, holidays, days_31_business, days_31_holidays
        """
        # Determinar si los sábados son hábiles basado en el contrato
        # Puedes agregar lógica adicional aquí si es necesario
        exclude_saturday = False
        if contract.employee_id and contract.employee_id.sabado:
            exclude_saturday = True
        
        lst_days = [5,6] if exclude_saturday else [6]
        
        current_date = date_from - timedelta(days=1)
        holidays = 0
        business_days = 0
        days_31_b = 0
        days_31_h = 0
        
        while current_date < date_to:
            date_add = current_date + timedelta(days=1)
            
            if not date_add.weekday() in lst_days:
                obj_holidays = self.env['lavish.holidays'].search([('date', '=', date_add)])
                
                if obj_holidays:
                    holidays += 1
                    days_31_h += 1 if date_add.day == 31 else 0
                else:
                    business_days += 1
                    days_31_b += 1 if date_add.day == 31 else 0
            else:
                holidays += 1
                days_31_h += 1 if date_add.day == 31 else 0
            
            current_date = date_add
        data = {
            'business_days': business_days - days_31_b,
            'holidays': holidays - days_31_h,
            'days_31_business': days_31_b,
            'days_31_holidays': days_31_h,
            'total_days': business_days + holidays
        }
        _logger.error(data)
        return data

    
    def _get_worked_day_lines_values(self, domain=None):
        res = super()._get_worked_day_lines_values(domain=domain)
        wm = self.env['hr.work.entry.type'].search([("code", "=", "VACATIONS_MONEY")], limit=1)
        vacations_money = self.env['hr.leave'].search([
            ('employee_id', '=', self.employee_id.id),
            ('holiday_status_id.code', '=', 'VACATIONS_MONEY'),
            ("request_date_from", ">=", self.date_from),
            ("request_date_to", "<=", self.date_to),
            ('state', '=', 'validate')
        ])
        if vacations_money:
            vacations_money_days = sum(vacations_money.mapped('number_of_days'))
            vacations_money_hours = vacations_money_days * self.contract_id.resource_calendar_id.hours_per_day
            res.append({
                'sequence': wm.sequence,
                'work_entry_type_id': wm.id,
                'number_of_days': vacations_money_days,
                'number_of_hours': vacations_money_hours,
            })
        return res

    def _get_new_worked_days_lines(self):
        if self.struct_id.use_worked_day_lines:
            # computation of the salary worked days
            worked_days_line_values = self._get_worked_day_lines()
            worked_days_lines = self.worked_days_line_ids.browse([])
            for r in worked_days_line_values:
                worked_days_lines |= worked_days_lines.new(r)
            february_worked_day = 0
            # Validar que al ser el mes de febrero modifique los días trabajados para que sean igual a un mes de 30 días
            if self.date_to.month == 2 and self.date_to.day in (28,29):
                february_worked_days = worked_days_lines.filtered(lambda l: l.work_entry_type_id.code == 'WORK100')
            days_summary = 2 if self.date_to.day == 28 else 1
            hours_summary = 16 if self.date_to.day == 28 else 8
            
            if len(february_worked_days) > 0:
                for february_days in worked_days_lines:
                    if february_days.work_entry_type_id.code == 'WORK100':
                        february_days.number_of_days = february_days.number_of_days + days_summary # Se agregan 2 días
                        february_days.number_of_hours = february_days.number_of_hours + hours_summary # Se agregan 16 horas
            else:
                #Ultimo día de febrero
                work_hours = self.contract_id._get_work_hours(self.date_to, self.date_to)
                work_hours_ordered = sorted(work_hours.items(), key=lambda x: x[1])
                biggest_work = work_hours_ordered[-1][0] if work_hours_ordered else 0
                #Primer día de marzo
                march_date_from = self.date_to + timedelta(days=1)
                march_date_to = self.date_to + timedelta(days=1)
                march_work_hours = self.contract_id._get_work_hours(march_date_from, march_date_to)
                march_work_hours_ordered = sorted(march_work_hours.items(), key=lambda x: x[1])
                march_biggest_work = march_work_hours_ordered[-1][0] if march_work_hours_ordered else 0
                #Proceso a realizar
                if march_biggest_work == 0 or biggest_work != march_biggest_work: #Si la ausencia no continua hasta marzo se agregan 2 días trabajados para completar los 30 días en febrero
                    work_entry_type = self.env['hr.work.entry.type'].search([('code','=','WORK100')])
                    attendance_line = {
                        'sequence': work_entry_type.sequence,
                        'work_entry_type_id': work_entry_type.id,
                        'number_of_days': days_summary,
                        'number_of_hours': hours_summary,
                        'amount': 0,
                    }
                    worked_days_lines |= worked_days_lines.new(attendance_line)
                else: #Si la ausencia continua hasta marzo se agregan 2 días de la ausencia para completar los 30 días en febrero
                    work_entry_type = self.env['hr.work.entry.type'].search([('id', '=', biggest_work)])
                    for february_days in worked_days_lines:
                        if february_days.work_entry_type_id.code == work_entry_type.code:
                            february_days.number_of_days = february_days.number_of_days + days_summary  # Se agregan 2 días
                            february_days.number_of_hours = february_days.number_of_hours + hours_summary  # Se agregan 16 horas
            if self.date_to.day == 31:
                worked_days = worked_days_lines.filtered(lambda l: l.work_entry_type_id.code == 'WORK100')
                days_summary = 1
                hours_summary = 8
                if len(worked_days) > 0:
                    for days in worked_days_lines:
                        if days.work_entry_type_id.code == 'WORK100':
                            days.number_of_days = days.number_of_days - days_summary # Se quita 1 días
                            days.number_of_hours = days.number_of_hours - hours_summary # Se quita 8 horas
            return worked_days_lines
        else:
            return [(5, False, False)]

    @api.depends('line_ids.total')
    def _compute_basic_net(self):
        line_values = (self._origin)._get_line_values(['BASIC', 'BASIC002', 'BASIC003', 'GROSS',  'TOTALDEV', 'NET'])
        for payslip in self:
            payslip.basic_wage = line_values['BASIC'][payslip._origin.id]['total'] + line_values['BASIC002'][payslip._origin.id]['total'] + line_values['BASIC003'][payslip._origin.id]['total']
            payslip.gross_wage = line_values['GROSS'][payslip._origin.id]['total'] + line_values['TOTALDEV'][payslip._origin.id]['total']
            payslip.net_wage = line_values['NET'][payslip._origin.id]['total']


    def action_payslip_done(self):
        if any(slip.state == 'cancel' for slip in self):
            raise ValidationError(_("You can't validate a cancelled payslip."))
        self.write({'state' : 'done', })
        self.mapped('payslip_run_id').action_close()
        pay_vacations_in_payroll = bool(self.env['ir.config_parameter'].sudo().get_param(
            'lavish_hr_payroll.pay_vacations_in_payroll')) or False
        
        self._action_create_account_move()
        #Actualizar en la tabla de prestamos la cuota pagada
        for record in self:
            if record.number == '/':
                record._set_next_sequence()
            for line in record.input_line_ids:
                if line.loan_line_id:
                    line.loan_line_id.paid = True
                    line.loan_line_id.payslip_id = record.id
                    line.loan_line_id.loan_id._compute_loan_amount()
            obj_payslip_line = self.env['hr.payslip.line'].search([('slip_id', '=', record.id),('loan_id', '!=', False)])
            for payslip_line in obj_payslip_line:
                obj_loan_line = self.env['hr.loan.installment'].search([('employee_id', '=', payslip_line.employee_id.id),('loan_id', '=', payslip_line.loan_id.id),
                                                                    ('date','>=',record.date_from),('date','<=',record.date_to)])
                data = {
                    'paid':True,
                    'payslip_id': record.id
                }
                obj_loan_line.write(data)
                
                obj_loan = self.env['hr.loan'].search([('employee_id', '=', payslip_line.employee_id.id),('id', '=', payslip_line.loan_id.id)])
                if obj_loan.pending_amount <= 0:
                    self.env['hr.contract.concepts'].search([('loan_id', '=', payslip_line.loan_id.id)]).write({'state':'cancel'})

            if record.struct_id.process == 'vacaciones' or (pay_vacations_in_payroll == True and record.struct_id.process != 'contrato'):
                history_vacation = []
                info_vacation = {}
                for line in sorted(record.line_ids.filtered(lambda filter: filter.initial_accrual_date), key=lambda x: x.initial_accrual_date):                
                    if line.code == 'VACDISFRUTADAS':
                        info_vacation = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'initial_accrual_date': line.initial_accrual_date,
                            'final_accrual_date': line.final_accrual_date,
                            'departure_date': record.date_from if not line.vacation_departure_date else line.vacation_departure_date,
                            'return_date': record.date_to if not line.vacation_return_date else line.vacation_return_date,
                            'business_units': line.business_units + line.business_31_units,
                            'value_business_days': line.business_units * line.amount,
                            'holiday_units': line.holiday_units + line.holiday_31_units,
                            'holiday_value': line.holiday_units * line.amount,                            
                            'base_value': line.amount_base,
                            'total': (line.business_units * line.amount)+(line.holiday_units * line.amount),
                            'payslip': record.id,
                            'leave_id': False if not line.vacation_leave_id else line.vacation_leave_id.id
                        }
                    if line.code == 'VACREMUNERADAS':
                        info_vacation = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'initial_accrual_date': line.initial_accrual_date,
                            'final_accrual_date': line.final_accrual_date,
                            'departure_date': record.date_from,
                            'return_date': record.date_to,                            
                            'units_of_money': line.quantity,
                            'money_value': line.total,
                            'base_value_money': line.amount_base,
                            'total': line.total,
                            'payslip': record.id
                        }
                    if line.code == 'VACATIONS_MONEY':
                        info_vacation = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'initial_accrual_date': line.initial_accrual_date,
                            'final_accrual_date': line.final_accrual_date,
                            'departure_date': record.date_from,
                            'return_date': record.date_to,                            
                            'units_of_money': line.quantity,
                            'money_value': line.total,
                            'base_value_money': line.amount_base,
                            'total': line.total,
                            'payslip': record.id
                        }

                    if pay_vacations_in_payroll == True:
                        #Si el historico ya existe no vuelva a crearlo
                        obj_history_vacation_exists = self.env['hr.vacation'].search([('employee_id','=',record.employee_id.id),
                                                                                      ('contract_id','=',record.contract_id.id),
                                                                                      ('initial_accrual_date','=',line.initial_accrual_date),
                                                                                      ('final_accrual_date','=',line.final_accrual_date),
                                                                                      ('leave_id','=',line.vacation_leave_id.id)])
                        if len(obj_history_vacation_exists) == 0:
                            history_vacation.append(info_vacation)
                    else:
                        history_vacation.append(info_vacation)

                if history_vacation: 
                    for history in history_vacation:
                        self.env['hr.vacation'].create(history) 

            if record.struct_id.process == 'cesantias' or record.struct_id.process == 'intereses_cesantias':
                his_cesantias = {}         
                his_intcesantias = {}

                for line in record.line_ids:                
                    #Historico cesantias                
                    if line.code == 'CESANTIAS' and line.is_history_reverse == False:
                        his_cesantias = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'type_history': 'cesantias',
                            'initial_accrual_date': record.date_from,
                            'final_accrual_date': record.date_to,
                            'settlement_date': record.date_to,                        
                            'time': line.quantity,
                            'base_value':line.amount_base,
                            'severance_value': line.total,                        
                            'payslip': record.id
                        }             

                    if line.code == 'INTCESANTIAS' and line.is_history_reverse == False:
                        his_intcesantias = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'type_history': 'intcesantias',
                            'initial_accrual_date': record.date_from,
                            'final_accrual_date': record.date_to,
                            'settlement_date': record.date_to,
                            'time': line.quantity,
                            'base_value': line.amount_base,
                            'severance_interest_value': line.total,
                            'payslip': record.id
                        }

                info_cesantias = {**his_cesantias,**his_intcesantias}        
                if info_cesantias:
                    self.env['hr.history.cesantias'].create(info_cesantias) 

            if record.struct_id.process == 'prima':            
                for line in record.line_ids:                
                    if line.code == 'PRIMA':
                        his_prima = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'initial_accrual_date': record.date_from,
                            'final_accrual_date': record.date_to,
                            'settlement_date': record.date_to,  
                            'time': line.quantity,
                            'base_value':line.amount_base,
                            'bonus_value': line.total,                        
                            'payslip': record.id                      
                        }
                        self.env['hr.history.prima'].create(his_prima) 

            if record.struct_id.process == 'contrato' or record.struct_id.process == 'nomina':  
                his_cesantias = {}         
                his_intcesantias = {}

                for line in record.line_ids:                                
                    #Historico vacaciones
                    if line.code == 'VACCONTRATO':
                        info_vacation = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'initial_accrual_date': line.initial_accrual_date,
                            'final_accrual_date': line.final_accrual_date,
                            'departure_date': record.date_liquidacion,
                            'return_date': record.date_liquidacion,                            
                            'units_of_money': (line.quantity*15)/360,
                            'money_value': line.total,
                            'base_value_money': line.amount_base,
                            'total': line.total,
                            'payslip': record.id
                        }
                        self.env['hr.vacation'].create(info_vacation) 
                    
                    #Historico prima
                    if line.code == 'PRIMA':
                        his_prima = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'initial_accrual_date': record.date_prima,
                            'final_accrual_date': record.date_liquidacion,
                            'settlement_date': record.date_liquidacion,  
                            'time': line.quantity,
                            'base_value':line.amount_base,
                            'bonus_value': line.total,                        
                            'payslip': record.id                      
                        }
                        self.env['hr.history.prima'].create(his_prima) 

                    #Historico cesantias                
                    if line.code == 'CESANTIAS' and line.is_history_reverse == False:
                        his_cesantias = {
                            'employee_id': record.employee_id.id,
                            'contract_id': record.contract_id.id,
                            'initial_accrual_date': record.date_cesantias,
                            'final_accrual_date': record.date_liquidacion,
                            'settlement_date': record.date_liquidacion,                        
                            'time': line.quantity,
                            'base_value':line.amount_base,
                            'severance_value': line.total,                        
                            'payslip': record.id
                        }               

                    if line.code == 'INTCESANTIAS' and line.is_history_reverse == False:
                        his_intcesantias = {
                            'severance_interest_value': line.total,
                        }

                info_cesantias = {**his_cesantias,**his_intcesantias}        
                if info_cesantias:
                    self.env['hr.history.cesantias'].create(info_cesantias) 

                if record.struct_id.process == 'contrato':
                    obj_contrato = self.env['hr.contract'].search([('id','=',record.contract_id.id)])
                    values_update = {'retirement_date':record.date_liquidacion,
                                    'state':'close'}
                    obj_contrato.write(values_update) 

            #Validar Historico de cesantias/int.cesantias a tener encuenta
            #Una vez confirmado va a la liquidacion asociado y deja en 0 el valor de CESANTIAS y INT CESANTIAS
            #Para evitar la duplicidad de los valores ya que fueron heredados a esta liquidación
            for payments in record.severance_payments_reverse:
                if payments.payslip:
                    value_cesantias = 0
                    value_intcesantias = 0
                    for line in payments.payslip.line_ids:
                        if line.code == 'CESANTIAS':
                            value_cesantias = line.total
                            line.write({'amount':0})
                        if line.code == 'INTCESANTIAS':
                            value_intcesantias = line.total
                            line.write({'amount':0})
                        if line.code == 'NET':
                            amount = line.total - (value_cesantias+value_intcesantias)
                            line.write({'amount':amount})

                    if payments.payslip.observation:
                        payments.payslip.write({'observation':payments.payslip.observation+ '\n El valor se trasladó a la liquidación '+ record.number+' de '+record.struct_id.name })
                    else:
                        payments.payslip.write({'observation': 'El valor se trasladó a la liquidación ' + record.number + ' de ' + record.struct_id.name})

class ResourceCalendar(models.Model):
    _inherit = "resource.calendar"

    def get_holidays(self, year, add_offset=False):
        self.ensure_one()
        leave_obj = self.env['resource.calendar.leaves']
        holidays = []
        tz_offset = 0
        if add_offset:
            tz_offset = fields.Datetime.context_timestamp(
                self, fields.Datetime.from_string(fields.Datetime.now())).\
                utcoffset().total_seconds()
        start_dt = fields.Datetime.from_string(fields.Datetime.now()).\
            replace(year=year, month=1, day=1, hour=0, minute=0, second=0) + \
            relativedelta(seconds=tz_offset)
        end_dt = start_dt + relativedelta(years=1) - relativedelta(seconds=1)
        leaves_domain = [
            ('calendar_id', '=', self.id),
            ('resource_id', '=', False),
            ('date_from', '>=', fields.Datetime.to_string(start_dt)),
            ('date_to', '<=', fields.Datetime.to_string(end_dt))]
        for leave in leave_obj.search(leaves_domain):
            date_from = fields.Datetime.from_string(leave.date_from)
            holidays.append((date_from.date(), leave.name))
        return holidays


class HrPayslipRuleOverride(models.Model):
    _name = 'hr.payslip.rule.override'
    _description = 'Modificación de Reglas de Nómina'

    payslip_id = fields.Many2one('hr.payslip', 'Nómina', required=True)
    rule_id = fields.Many2one('hr.salary.rule', 'Regla', required=True)
    override_type = fields.Selection([
        ('amount', 'Monto Base'),
        ('quantity', 'Cantidad'),
        ('rate', 'Tasa'),
        ('total', 'Total Final'),
    ], string='Tipo de Modificación', required=True)
    value_original = fields.Float('Valor Original', readonly=True)
    value_override = fields.Float('Valor Nuevo')
    active = fields.Boolean('Aplicar Modificación', default=True)
    description = fields.Text('Descripción/Motivo')
    simulation_date = fields.Datetime('Fecha Simulación', default=fields.Datetime.now)
    simulation_result = fields.Float('Resultado Simulado', readonly=True)
    difference = fields.Float('Diferencia', compute='_compute_difference')
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('simulated', 'Simulado'),
        ('applied', 'Aplicado')
    ], string='Estado', default='draft')

    @api.depends('value_original', 'value_override')
    def _compute_difference(self):
        for record in self:
            record.difference = record.value_override - record.value_original

    def action_simulate(self):
        self.ensure_one()
        if self.payslip_id.state not in ['draft', 'verify']:
            raise UserError('Solo se pueden simular ajustes en nóminas en borrador o verificación')
        
        # Crear una copia del cálculo original para simular
        result = self.payslip_id.with_context(
            simulate_override=True, 
            override_rule=self.rule_id.code,
            override_type=self.override_type,
            override_value=self.value_override
        )._get_payslip_lines_lavish()
        
        # Encontrar el resultado simulado para esta regla
        rule_result = next((r for r in result if r['code'] == self.rule_id.code), None)
        if rule_result:
            self.simulation_result = rule_result['total']
            self.state = 'simulated'
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Simulación Completada',
                'message': f'Resultado simulado: {self.simulation_result:,.2f}\nDiferencia: {self.difference:,.2f}',
                'sticky': False,
                'type': 'info'
            }
        }


    @api.onchange('value_override')
    def _onchange_value_override(self):
        if self.value_override and self.value_original:
            if abs((self.value_override - self.value_original) / self.value_original) > 0.5:
                return {
                    'warning': {
                        'title': _("Variación Significativa"),
                        'message': _("El ajuste representa una variación mayor al 50% del valor original. Por favor verifique.")
                    }
                }