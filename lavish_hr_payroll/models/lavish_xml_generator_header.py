# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime

class LavishXmlGeneratorHeader(models.Model):
    _name = 'lavish.xml.generator.header'
    _description = 'Generador XML Nómina Electrónica (Header)'

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)  # ej: NomElectronica_Carvajal
    active = fields.Boolean(default=True)

    # ⬇️ ESTE ES EL CAMPO QUE FALTA
    details_ids = fields.One2many(
        'lavish.xml.generator.detail', 'header_id', string='Detalles (tags)'
    )

    def xml_generator(self, detail_record):
        """Fallback mínimo si no tienes plantillas reales."""
        if not hasattr(detail_record, '_build_xml_default'):
            raise ValidationError(_("No hay generador de XML disponible."))
        return detail_record._build_xml_default()


class LavishXmlGeneratorDetail(models.Model):
    _name = 'lavish.xml.generator.detail'
    _description = 'Generador XML Nómina (Detalle/Tag)'

    header_id = fields.Many2one(
        'lavish.xml.generator.header', ondelete='cascade', required=True
    )
    sequence = fields.Integer(default=10)
    # El código del módulo original consulta estas dos:
    code_python = fields.Text(string="Códigos de regla a incluir (texto)")
    attributes_code_python = fields.Text(string="Atributos/alias (texto)")
