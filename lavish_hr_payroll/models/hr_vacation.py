# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from .browsable_object import BrowsableObject, InputLine, WorkedDays, Payslips
from odoo.tools import float_compare, float_is_zero

from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

import logging

_logger = logging.getLogger(__name__)

class hr_vacation(models.Model):
    _name = 'hr.vacation'
    _description = 'Historico de vacaciones'
    
    vacation_type = fields.Selection([
        ('enjoy', 'Disfrute'),
        ('money', 'En Dinero')
    ], string='Tipo de Vacaciones', compute='_compute_vacation_type', store=True)
    employee_id = fields.Many2one('hr.employee', 'Empleado')
    employee_identification = fields.Char('Identificación empleado')
    initial_accrual_date = fields.Date('Fecha inicial de causación')
    final_accrual_date = fields.Date('Fecha final de causación')
    departure_date = fields.Date('Fechas salida')
    return_date = fields.Date('Fecha regreso')
    base_value = fields.Float('Base vacaciones disfrutadas')
    base_value_money = fields.Float('Base vacaciones remuneradas')
    business_units = fields.Float('Unidades hábiles')
    value_business_days = fields.Float('Valor días hábiles')
    holiday_units = fields.Float('Unidades festivos')
    holiday_value = fields.Float('Valor días festivos')
    units_of_money = fields.Float('Unidades dinero')
    money_value = fields.Float('Valor en dinero')
    total = fields.Float('Total')
    remaining_days = fields.Float('Días restantes', compute='_compute_remaining_days')
    payslip = fields.Many2one('hr.payslip', 'Liquidación')
    leave_id = fields.Many2one('hr.leave', 'Ausencia')
    contract_id = fields.Many2one('hr.contract', 'Contrato')
    description = fields.Char('Contrato')

    @api.depends('leave_id.holiday_status_id')
    def _compute_vacation_type(self):
        for record in self:
            if record.leave_id and record.leave_id.holiday_status_id:
                if record.leave_id.holiday_status_id.is_vacation_money:
                    record.vacation_type = 'money'
                else:
                    record.vacation_type = 'enjoy'
            else:
                if record.units_of_money != 0:
                    record.vacation_type = 'money'
                else:
                    record.vacation_type = 'enjoy'

    @api.depends('departure_date', 'return_date', 'vacation_type')
    def _compute_remaining_days(self):
        for record in self:
            if record.contract_id:
                remaining = record.contract_id.calculate_remaining_days(
                    ignore_payslip_id=record.payslip.id if record.payslip else None
                )
                record.remaining_days = remaining
            else:
                record.remaining_days = 0

    def name_get(self):
        result = []
        for record in self:
            result.append((record.id, "Vacaciones {} del {} al {}".format(
                record.employee_id.name, str(record.departure_date), str(record.return_date)
            )))
        return result
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Handle employee identification lookup
            if vals.get('employee_identification'):
                obj_employee = self.env['hr.employee'].search(
                    [('identification_id', '=', vals.get('employee_identification'))],
                    limit=1
                )
                if obj_employee:
                    vals['employee_id'] = obj_employee.id
            
            # Handle employee id lookup
            if vals.get('employee_id'):
                obj_employee = self.env['hr.employee'].search(
                    [('id', '=', vals.get('employee_id'))],
                    limit=1
                )
                if obj_employee:
                    vals['employee_identification'] = obj_employee.identification_id

        return super().create(vals_list)

    def get_paid_vacations(self, contract_id, ignore_payslip_id):
        domain = [('contract_id', '=', contract_id)]
        if ignore_payslip_id:
            domain.append(('payslip', '!=', ignore_payslip_id))
        
        vacations = self.env['hr.vacation'].search(domain)
        total_days = 0
        for v in vacations:
            if v.vacation_type == 'enjoy':
                total_days += v.business_units
            elif v.vacation_type == 'money':
                total_days += v.units_of_money
        return total_days

    def calculate_vacation_days(self, working_days, unpaid_days):
        VACATION_FACTOR = 15
        YEAR_DAYS = 360
        return ((working_days - unpaid_days) * VACATION_FACTOR) / YEAR_DAYS

    def calculate_remaining_days(self, ignore_payslip_id=None, method_old=False):
        if self.days_left > 0 and self.date_ref_holiday_book:
            initial_days = self.days_left
            date_start = self.date_ref_holiday_book
        else:
            initial_days = 0
            date_start = self.date_start

        date_end = self.retirement_date or fields.Date.today()
        employee_id = self.employee_id.id

        if method_old:
            days_service = self.dias360(date_start, date_end)
            days_unpaid = self.get_unpaid_absences(date_start, date_end, employee_id)
            days_vacations_total = self.calculate_vacation_days(days_service, days_unpaid)
            days_paid = self.get_paid_vacations(self.id, ignore_payslip_id)
            return round(initial_days + days_vacations_total - days_paid, 2)
        else:
            date_vacation = date_start
            domain = [
                ('employee_id', '=', employee_id),
                ('contract_id', '=', self.id)
            ]
            if ignore_payslip_id:
                domain.append(('payslip', '!=', ignore_payslip_id))

            vacation_history = self.env['hr.vacation'].search(domain)

            if vacation_history:
                for history in sorted(vacation_history, key=lambda x: x.final_accrual_date):
                    if history.final_accrual_date and (not history.leave_id or not history.leave_id.holiday_status_id.unpaid_absences):
                        if history.final_accrual_date > date_vacation:
                            date_vacation = history.final_accrual_date + timedelta(days=1)

            working_days = self.dias360(date_vacation, date_end)
            unpaid_days = self.get_unpaid_absences(date_vacation, date_end, employee_id)
            current_period_days = self.calculate_vacation_days(working_days, unpaid_days)
            total_days = initial_days + current_period_days

            return round(total_days, 2)

class hr_payslip_paid_vacation(models.Model):
    _name = 'hr.payslip.paid.vacation'
    _description = 'Liquidación vacaciones remuneradas'

    slip_id = fields.Many2one('hr.payslip',string='Nómina', required=True)
    paid_vacation_days = fields.Integer(string='Cantidad de días', required=True)
    start_date_paid_vacation = fields.Date(string='Fecha inicial', required=True)
    end_date_paid_vacation = fields.Date(string='Fecha final', required=True)

    @api.onchange('paid_vacation_days','start_date_paid_vacation')
    def _onchange_paid_vacation_days(self):
        for record in self:
            if record.paid_vacation_days > 0 and record.start_date_paid_vacation:
                date_to = record.start_date_paid_vacation - timedelta(days=1)
                cant_days = record.paid_vacation_days
                days = 0
                days_31 = 0
                while cant_days > 0:
                    date_add = date_to + timedelta(days=1)
                    cant_days = cant_days - 1
                    days += 1
                    days_31 += 1 if date_add.day == 31 else 0
                    date_to = date_add

                record.end_date_paid_vacation = date_to
                record.paid_vacation_days = days - days_31

class Hr_payslip_line(models.Model):
    _inherit = 'hr.payslip.line'

    initial_accrual_date = fields.Date('C. Inicio')
    final_accrual_date = fields.Date('C. Fin')
    vacation_departure_date = fields.Date('Fechas salida vacaciones')
    vacation_return_date = fields.Date('Fechas regreso vacaciones')
    vacation_leave_id = fields.Many2one('hr.leave', 'Ausencia')
    business_units = fields.Float('Unidades hábiles')
    business_31_units = fields.Float('Unidades hábiles - Días 31')
    holiday_units = fields.Float('Unidades festivos')
    holiday_31_units = fields.Float('Unidades festivos  - Días 31')