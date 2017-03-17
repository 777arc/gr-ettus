#
# Copyright 2013 Free Software Foundation, Inc.
#
# This file is part of GNU Radio
#
# GNU Radio is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
#
# GNU Radio is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GNU Radio; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
#
""" Module to add new blocks """

import os
import re
from optparse import OptionGroup

from util_functions import append_re_line_sequence, ask_yes_no, id_process
from cmakefile_editor import CMakeFileEditor
from modtool_base import ModTool, ModToolException
from templates import Templates
from code_generator import get_template
import Cheetah.Template


class ModToolAdd(ModTool):
    """ Add block to the out-of-tree module. """
    name = 'add'
    aliases = ('insert',)

    def __init__(self):
        ModTool.__init__(self)
        self._skip_cmakefiles = False
        self._skip_block_ctrl = False
        self._skip_block_interface = False
        self._license_file = None

    def setup_parser(self):
        parser = ModTool.setup_parser(self)
        ogroup = OptionGroup(parser, "Add module options")
        ogroup.add_option("--license-file", type="string", default=None,
                          help="File containing the license header for every source code file.")
        ogroup.add_option("--noc_id", type="string", default=None,
                help="The ID number with which the RFNoC block will identify itself at the SW/Hw interface")
        ogroup.add_option("--copyright", type="string", default=None,
                help="Name of the copyright holder (you or your company) MUST be a quoted string.")
        ogroup.add_option("--argument-list", type="string", default=None,
                help="The argument list for the constructor and make functions.")
        ogroup.add_option("--skip-cmakefiles", action="store_true", default=False,
                help="If given, only source files are written, but CMakeLists.txt files are left unchanged.")
        ogroup.add_option("--skip-block-ctrl", action="store_true", default=None,
                help="If given, skips the generation of the RFNoC Block Controllers.")
        ogroup.add_option("--skip-block-interface", action="store_true", default=None,
                help="If given, skips the generation of the RFNoC interface files.")
        parser.add_option_group(ogroup)
        return parser

    def setup(self, options, args):
        ModTool.setup(self, options, args)
        self._info['blocktype'] = 'rfnoc'
        self._info['lang'] = 'cpp'
        if (self._skip_subdirs['lib']) or (self._skip_subdirs['python']):
            raise ModToolException('Missing or skipping relevant subdir.')
        if self._info['blockname'] is None:
            if len(args) >= 2:
                self._info['blockname'] = args[1]
            else:
                self._info['blockname'] = raw_input("Enter name of block/code (without module name prefix): ")
        if not re.match('^([a-zA-Z]+[0-9a-zA-Z]*)$', self._info['blockname']):
            raise ModToolException('Invalid block name.')
        print("Block/code identifier: " + self._info['blockname'])
        self._info['fullblockname'] = self._info['modname'] + '_' + self._info['blockname']
        if not options.license_file:
            self._info['copyrightholder'] = options.copyright
            if self._info['copyrightholder'] is None:
                self._info['copyrightholder'] = '<+YOU OR YOUR COMPANY+>'
            elif self._info['is_component']:
                print("For GNU Radio components the FSF is added as copyright holder")
        self._license_file = options.license_file
        self._info['license'] = self.setup_choose_license()
        if options.argument_list is not None:
            self._info['arglist'] = options.argument_list
        else:
            self._info['arglist'] = raw_input('Enter valid argument list, including default arguments: ')

        self._skip_cmakefiles = options.skip_cmakefiles
        if self._info['version'] == 'autofoo' and not self._skip_cmakefiles:
            print("Warning: Autotools modules are not supported. ",)
            print("Files will be created, but Makefiles will not be edited.")
            self._skip_cmakefiles = True
        #NOC ID parse
        self._info['noc_id'] = options.noc_id
        if self._info['noc_id'] is None:
            self._info['noc_id'] = id_process(raw_input("Block NoC ID (Hexadecimal): "))
        if not re.match(r'\A[0-9A-F]+\Z', self._info['noc_id']):
            raise ModToolException('Invalid NoC ID - Only Hexadecimal Values accepted.')
        self._skip_block_ctrl = options.skip_block_ctrl
        if self._skip_block_ctrl is None:
            self._skip_block_ctrl = ask_yes_no('Skip Block Controllers Generation? [UHD block ctrl files]', False)
        self._skip_block_interface = options.skip_block_interface
        if self._skip_block_interface is None:
            self._skip_block_interface = ask_yes_no('Skip Block interface files Generation? [GRC block ctrl files]', False)

    def setup_choose_license(self):
        """ Select a license by the following rules, in this order:
        1) The contents of the file given by --license-file
        2) The contents of the file LICENSE or LICENCE in the modules
           top directory
        3) The default license. """
        if self._license_file is not None \
            and os.path.isfile(self._license_file):
            return open(self._license_file).read()
        elif os.path.isfile('LICENSE'):
            return open('LICENSE').read()
        elif os.path.isfile('LICENCE'):
            return open('LICENCE').read()
        elif self._info['is_component']:
            return Templates['grlicense']
        else:
            return get_template('defaultlicense', **self._info)

    def _write_tpl(self, tpl, path, fname):
        """ Shorthand for writing a substituted template to a file"""
        path_to_file = os.path.join(path, fname)
        print("Adding file '%s'..." % path_to_file)
        open(path_to_file, 'w').write(get_template(tpl, **self._info))
        self.scm.add_files((path_to_file,))

    def run(self):
        """ Go, go, go. """
        has_swig = (
                self._info['lang'] == 'cpp'
                and not self._skip_subdirs['swig']
        )
        has_grc = False
        self._run_lib()
        has_grc = has_swig
        if has_swig:
            self._run_swig()
        if has_grc and not self._skip_subdirs['grc']:
            self._run_grc()
        self._run_rfnoc()

    def _run_lib(self):
        """ Do everything that needs doing in the subdir 'lib' and 'include'.
        - add .cc and .h files
        - include them into CMakeLists.txt
        """
        fname_cc = None
        fname_h  = None
        if self._info['version']  == '37':
            #RFNoC block Interface
            if(self._skip_block_interface == False):
                fname_h  = self._info['blockname'] + '.h'
                fname_cc = self._info['blockname'] + '.cc'
                fname_cc = self._info['blockname'] + '_impl.cc'
                self._write_tpl('block_impl_h',   'lib', self._info['blockname'] + '_impl.h')
                self._write_tpl('block_impl_cpp', 'lib', fname_cc)
                self._write_tpl('block_def_h',    self._info['includedir'], fname_h)
                if not self._skip_cmakefiles:
                    ed = CMakeFileEditor(self._file['cmlib'])
                    cmake_list_var = '[a-z]*_?' + self._info['modname'] + '_sources'
                    if not ed.append_value('list', fname_cc, to_ignore_start='APPEND ' + cmake_list_var):
                        ed.append_value('add_library', fname_cc)
                    ed.write()
                    ed = CMakeFileEditor(self._file['cminclude'])
                    ed.append_value('install', fname_h, to_ignore_end='DESTINATION[^()]+')
                    ed.write()
                    self.scm.mark_files_updated((self._file['cminclude'], self._file['cmlib']))
            #RFNoC block Controllers
            if (self._skip_block_ctrl == False):
                fname_ctrl_cpp = self._info['blockname'] + '_block_ctrl_impl.cpp'
                fname_ctrl_hpp = self._info['blockname'] + '_block_ctrl.hpp'
                self._write_tpl('block_ctrl_hpp',    self._info['includedir'], fname_ctrl_hpp)
                self._write_tpl('block_ctrl_cpp', 'lib', fname_ctrl_cpp)
                if not self._skip_cmakefiles:
                    ed = CMakeFileEditor(self._file['cmlib'])
                    cmake_list_var = '[a-z]*_?' + self._info['modname'] + '_sources'
                    ed.append_value('list', fname_ctrl_cpp, to_ignore_start='APPEND ' + cmake_list_var)
                    ed.write()
                    ed = CMakeFileEditor(self._file['cminclude'])
                    ed.append_value('install', fname_ctrl_hpp, to_ignore_end='DESTINATION[^()]+')
                    ed.write()
                    self.scm.mark_files_updated((self._file['cminclude'], self._file['cmlib']))
        else: # Pre-3.7 or autotools
            fname_h  = self._info['fullblockname'] + '.h'
            fname_cc = self._info['fullblockname'] + '.cc'
            self._write_tpl('block_h36',   self._info['includedir'], fname_h)
            self._write_tpl('block_cpp36', 'lib',                    fname_cc)

    def _run_swig(self):
        """ Do everything that needs doing in the subdir 'swig'.
        - Edit main *.i file
        """
        if self._get_mainswigfile() is None:
            print('Warning: No main swig file found.')
            return
        print("Editing %s..." % self._file['swig'])
        mod_block_sep = '/'
        if self._info['version'] == '36':
            mod_block_sep = '_'
        swig_block_magic_str = get_template('swig_block_magic', **self._info)
        open(self._file['swig'], 'a').write(swig_block_magic_str)
        include_str = '#include "%s%s%s.h"' % (
                {True: 'gnuradio/' + self._info['modname'], False: self._info['modname']}[self._info['is_component']],
                mod_block_sep,
                self._info['blockname'])
        if re.search('#include', open(self._file['swig'], 'r').read()):
            append_re_line_sequence(self._file['swig'], '^#include.*\n', include_str)
        else: # I.e., if the swig file is empty
            oldfile = open(self._file['swig'], 'r').read()
            regexp = re.compile('^%\{\n', re.MULTILINE)
            oldfile = regexp.sub('%%{\n%s\n' % include_str, oldfile, count=1)
            open(self._file['swig'], 'w').write(oldfile)
        self.scm.mark_files_updated((self._file['swig'],))

    def _run_grc(self):
        """ Do everything that needs doing in the subdir 'grc' to add
        a GRC bindings XML file.
        - add .xml file
        - include in CMakeLists.txt
        """
        fname_grc = self._info['fullblockname'] + '.xml'
        self._write_tpl('grc_xml', 'grc', fname_grc)
        ed = CMakeFileEditor(self._file['cmgrc'], '\n    ')
        if self._skip_cmakefiles or ed.check_for_glob('*.xml'):
            return
        print("Editing grc/CMakeLists.txt...")
        ed.append_value('install', fname_grc, to_ignore_end='DESTINATION[^()]+')
        ed.write()
        self.scm.mark_files_updated((self._file['cmgrc'],))

    def _run_rfnoc(self):
        """ Do everything that needs doing in the subdir 'rfnoc' to add
        a GRC block control bindings XML file.
        - add .xml file
        - include in CMakeLists.txt
        - add verilog file
        - adds verilog name to Makefile.srcs
        - Calls _run_testbenches()
        - Runs build (test)
        """
        fname_rfnoc = self._info['blockname'] + '.xml'
        fname_rfnocv = 'noc_block_' +  self._info['blockname'] + '.v'
        self._write_tpl('rfnoc_xml', 'rfnoc/blocks', fname_rfnoc)
        self._write_tpl('rfnoc_v', 'rfnoc/fpga-src', fname_rfnocv)
        patt_v = re.escape('$(addprefix SOURCES_PATH, \\\n')
        append_re_line_sequence(self._file['rfnoc_mksrc'],
                                           patt_v,
                                           'noc_block_' + self._info['blockname'] + '.v \\')
        ed = CMakeFileEditor(self._file['cmrfnoc'], '\n    ')
        self._run_testbenches()
        self._build()
        if self._skip_cmakefiles or ed.check_for_glob('*.xml'):
            return
        print("Editing rfnoc/blocks/CMakeLists.txt...")
        ed.append_value('install', fname_rfnoc, to_ignore_end='DESTINATION[^()]+')
        ed.write()
        self.scm.mark_files_updated((self._file['cmrfnoc'],))

    def _run_testbenches(self):
        """
        Generates the template for the OOT mod testbench and puts it into
        a new folder along with an empty CMakelists and a template for
        the Makefile.
        """
        dirname = 'noc_block_' + self._info['blockname'] + '_tb'
        new_tbdir = 'rfnoc/testbenches/' + dirname
        if not os.path.isdir(new_tbdir):
            os.makedirs(new_tbdir)
            print(new_tbdir + ' folder created')
        tbname = 'noc_block_' + self._info['blockname'] + '_tb.sv'
        self._write_tpl('rfnoc_tb', new_tbdir, tbname)
        self._write_tpl('tb_makefile', new_tbdir, 'Makefile')
        self._write_tpl('empty', new_tbdir, 'CMakeLists.txt')
        append_re_line_sequence('rfnoc/testbenches/CMakeLists.txt',
               "--------------------",
               'add_subdirectory({})'.format(dirname) + '\n')

    def _build(args):
        """
        Run the make command that sets up the fpga repository path. Assumes
        that the  script is run in the newly created OOT mod, which is the
        normal operational behavior
        """
        cwd = os.getcwd()
        build_dir = os.path.join(cwd, 'build')
        if os.path.isdir(build_dir):
            print("changing temporarily working directory to {0}".\
                    format(build_dir))
            os.chdir(build_dir)
            make_cmd = "make test_tb"
            ret_val = os.system(make_cmd)
            os.chdir(cwd)
            return ret_val
