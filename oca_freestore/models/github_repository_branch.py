# -*- coding: utf-8 -*-
# Copyright (C) 2016-Today: Odoo Community Association (OCA)
# @author: Sylvain LE GAL (https://twitter.com/legalsylvain)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import logging
import os

from subprocess import check_output
from datetime import datetime
from os.path import join as opj

from openerp import models, fields, api, exceptions, _

from openerp.modules import load_information_from_description_file
from openerp.modules.module import MANIFEST

_logger = logging.getLogger(__name__)


class GithubRepositoryBranch(models.Model):
    _name = 'github.repository.branch'
    _inherit = ['github.connector']
    _order = 'complete_name'

    _SELECTION_STATE = [
        ('to_download', 'To Download'),
        ('to_analyze', 'To Analyze'),
        ('analyzed', 'Analyzed'),
    ]

    # Column Section
    name = fields.Char(
        string='Name', select=True, required=True, readonly=True)

    complete_name = fields.Char(
        string='Complete Name', compute='_compute_complete_name', store=True)

    state = fields.Selection(
        string='State', selection=_SELECTION_STATE, default='to_download')

    repository_id = fields.Many2one(
        comodel_name='github.repository', string='Repository',
        required=True, select=True, readonly=True, ondelete='cascade')

    organization_id = fields.Many2one(
        comodel_name='github.organization', string='Organization',
        related='repository_id.organization_id', store=True,
        readonly=True)

    last_download_date = fields.Datetime(string='Last Download Date')

    last_analyze_date = fields.Datetime(string='Last Analyze Date')

    module_paths = fields.Text(
        string='Module Paths', help="Set here extra relative paths"
        " you want to scan to find modules. If not set, root path will be"
        " scanned. One repository per line. Exemple:\n"
        "./addons/\n"
        "./openerp/addons/")

    module_version_ids = fields.One2many(
        comodel_name='oca.module.version',
        inverse_name='repository_branch_id', string='Module Versions')

    module_version_qty = fields.Integer(
        string='Module Versions Quantity',
        compute='compute_module_version_qty')

    # Compute Section
    @api.multi
    @api.depends('name', 'repository_id.complete_name')
    def _compute_complete_name(self):
        for repository_branch in self:
            repository_branch.complete_name =\
                repository_branch.repository_id.complete_name +\
                '/' + repository_branch.name

    # Compute Section
    @api.one
    def name_get(self):
        return [self.id, self.complete_name]

    @api.multi
    @api.depends(
        'module_version_ids', 'module_version_ids.repository_branch_id')
    def compute_module_version_qty(self):
        for repository_branch in self:
            repository_branch.module_version_qty =\
                len(repository_branch.module_version_ids)

    # Action Section
    @api.multi
    def button_download_code(self):
        return self._download_code()

    @api.multi
    def button_update_code(self):
        return self._download_code()

    @api.multi
    def button_analyze_code(self):
        return self._analyze_code()

    # Custom Section
    def create_or_update_from_name(self, repository_id, name):
        repository_branch = self.search([
            ('name', '=', name), ('repository_id', '=', repository_id)])
        if not repository_branch:
            repository_branch = self.create({
                'name': name,
                'repository_id': repository_id})
        return repository_branch

    def _download_code(self):
        for repository_branch in self:
            path = self._get_local_path(repository_branch.complete_name)
            if not os.path.exists(path):
                _logger.info(
                    "Cloning new repository into %s ..." % (path))
                # Cloning the repository
                os.makedirs(path)
                os.system(
                    "cd %s &&"
                    " git clone https://github.com/%s.git -b %s ." % (
                        path,
                        repository_branch.repository_id.complete_name,
                        repository_branch.name))
                repository_branch.write({
                    'last_download_date': datetime.today(),
                    'state': 'to_analyze',
                    })
            else:
                # Update repository
                _logger.info(
                    "Pulling existing repository %s ..." % (path))
                try:
                    res = check_output(
                        ['git', 'pull', 'origin', repository_branch.name],
                        cwd=path)
                except:
                    raise exceptions.Warning(
                        _("Git Access Error"),
                        _("Unable to access to pull repository in %s.") % (
                            path))
                if repository_branch.state == 'to_download' or\
                        'up-to-date' not in res:
                    repository_branch.write({
                        'last_download_date': datetime.today(),
                        'state': 'to_analyze',
                        })
                else:
                    repository_branch.write({
                        'last_download_date': datetime.today(),
                        })
            self._cr.commit()

    @api.multi
    def _analyze_code(self):
        module_version_obj = self.env['oca.module.version']
        for repository_branch in self:
            # Delete all associated module versions
            module_versions = module_version_obj.search([
                ('repository_branch_id', '=', repository_branch.id)])
            module_versions.with_context(
                dont_change_repository_branch_state=True).unlink()

            # Compute path(s) to analyze
            if repository_branch.module_paths:
                paths = []
                for path in repository_branch.module_paths.split('\n'):
                    if path.strip():
                        paths.append(self._get_local_path(
                            repository_branch.complete_name) + '/' + path)
            else:
                paths = [self._get_local_path(repository_branch.complete_name)]
            # Scan each path, if exists
            for path in paths:
                if not os.path.exists(path):
                    _logger.warning(
                        "Unable to analyse %s. Source code not found." % (
                            path))
                else:
                    # Scan folder
                    _logger.info("Analyzing repository %s ..." % (path))
                    for module_name in self.listdir(path):
                        module_info = load_information_from_description_file(
                            module_name, path + '/' + module_name)
                        # Create module version, if the module is installable
                        # in the serie
                        if module_info.get('installable', False):
                            module_info['name'] = module_name
                            module_version_obj.create_or_update_from_manifest(
                                module_info, repository_branch)
                        self._cr.commit()
                    repository_branch.write({
                        'last_analyze_date': datetime.today(),
                        'state':  'analyzed',
                        })
            self._cr.commit()

    # Copy Paste from Odoo Core
    # This function is for the time being in another function.
    # (Ref: openerp/modules/module.py)
    def listdir(self, dir):
        def clean(name):
            name = os.path.basename(name)
            if name[-4:] == '.zip':
                name = name[:-4]
            return name

        def is_really_module(name):
            manifest_name = opj(dir, name, MANIFEST)
            return os.path.isfile(manifest_name)

        return map(clean, filter(is_really_module, os.listdir(dir)))
