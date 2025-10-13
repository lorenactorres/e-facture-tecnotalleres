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
    """Función auxiliar extendida para serializar varios tipos de objetos."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return float(obj)
    elif hasattr(obj, '__dict__'):
        return obj.__dict__
    raise TypeError(f"Type {type(obj)} not serializable")

class HrPayslip(models.Model):
    _inherit = 'hr.payslip'