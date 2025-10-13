# -*- coding: utf-8 -*-
from odoo import models, fields, _
from datetime import datetime
import base64

class HrElectronicPayrollDetailFallback(models.Model):
    _inherit = 'hr.electronic.payroll.detail'

    def _build_xml_default(self):
        """Generador XML mínimo por defecto (bytes UTF-8).
        NOTA: Estructura simplificada para que el flujo 'Envío' funcione.
        Ajusta los tags para cumplir 100% DIAN si vas a producción.
        """
        self.ensure_one()
        # Datos base
        company = self.electronic_payroll_id.company_id if hasattr(self, 'electronic_payroll_id') and self.electronic_payroll_id else False
        operator = (company.payroll_electronic_operator or 'Carvajal') if company else 'Carvajal'
        employee = self.employee_id
        ident = employee.identification_id or ''
        today = datetime.utcnow()
        fecha = today.strftime('%Y-%m-%d')
        hora  = today.strftime('%H:%M:%S-05:00')

        # Totales simples (si tienes payslips asociados, puedes sumar devengados/deducciones reales)
        # Aquí dejamos valores simbólicos para que el WS reciba algo coherente.
        devengados = 0.0
        deducciones = 0.0
        neto = 0.0
        if hasattr(self, 'payslip_ids') and self.payslip_ids:
            for slip in self.payslip_ids:
                devengados += getattr(slip, 'total_earnings', 0.0) or 0.0
                deducciones += getattr(slip, 'total_deductions', 0.0) or 0.0
                if hasattr(slip, 'net_wage'):
                    neto += (slip.net_wage or 0.0)
        if not (devengados or deducciones or neto):
            # fallback muy básico si no hay campos agregados
            neto = getattr(employee, 'wage', 0.0) or 0.0

        # XML mínimo (usa los namespaces comunes; ajusta según tu operador)
        # OJO: Si tu operador exige schemaLocation específico, tu código original ya hace replace para FacturaTech.
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<NominaIndividual xmlns="urn:co:facturaelectronica:NominaIndividual:1"
    xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    xmlns:xades="http://uri.etsi.org/01903/v1.3.2#"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="urn:co:facturaelectronica:NominaIndividual:1 NominaIndividualElectronicaXSD.xsd">
  <ext:UBLExtensions> </ext:UBLExtensions>
  <FechaEmision>{fecha}</FechaEmision>
  <HoraEmision>{hora}</HoraEmision>
  <Empleador>
    <NIT>{(company.vat or '').replace('-', '') if company else ''}</NIT>
    <RazonSocial>{(company.name or '') if company else ''}</RazonSocial>
  </Empleador>
  <Trabajador>
    <TipoDocumento>{employee.l10n_co_document_type or '13'}</TipoDocumento>
    <NumeroDocumento>{ident}</NumeroDocumento>
    <Apellidos>{(employee.lastname or employee.name or '').split()[-1]}</Apellidos>
    <Nombres>{(employee.firstname or employee.name or '').split()[0]}</Nombres>
  </Trabajador>
  <Periodo>
    <FechaIngreso>{(employee.first_contract_date or fecha)}</FechaIngreso>
    <FechaLiquidacion>{fecha}</FechaLiquidacion>
  </Periodo>
  <Devengados>
    <TotalDevengados>{devengados:.2f}</TotalDevengados>
  </Devengados>
  <Deducciones>
    <TotalDeducciones>{deducciones:.2f}</TotalDeducciones>
  </Deducciones>
  <Redondeo>0.00</Redondeo>
  <DevengadosTotal>{devengados:.2f}</DevengadosTotal>
  <DeduccionesTotal>{deducciones:.2f}</DeduccionesTotal>
  <ComprobanteTotal>{neto:.2f}</ComprobanteTotal>
</NominaIndividual>
'''.encode('utf-8')
        return xml


class HrElectronicAdjustPayrollDetailFallback(models.Model):
    _inherit = 'hr.electronic.adjust.payroll.detail'

    def _build_xml_default(self):
        """Generador sencillo para ajustes; similar al de arriba."""
        self.ensure_one()
        # Reutiliza el de nómina base si lo prefieres:
        base_detail = self.env['hr.electronic.payroll.detail'].new({})
        # Fake minimal fields to reuse logic, o copia y pega la lógica del anterior aquí.
        return self.env['hr.electronic.payroll.detail']._build_xml_default(self)
