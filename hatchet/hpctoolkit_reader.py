##############################################################################
# Copyright (c) 2017-2018, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
#
# This file is part of Hatchet.
# Created by Abhinav Bhatele <bhatele@llnl.gov>.
# LLNL-CODE-741008. All rights reserved.
#
# For details, see: https://github.com/LLNL/hatchet
# Please also read the LICENSE file for the MIT License notice.
##############################################################################

import glob
import struct
import numpy as np
import pandas as pd
# from __future__ import print_function
# import time

from node import Node
from graph import Graph

try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

src_file = 0
stmt_num = 1


class HPCToolkitReader:
    """
    Read in the various sections of an HPCToolkit experiment.xml file
    and metric-db files
    """

    def __init__(self, dir_name):
        self.dir_name = dir_name

        root = ET.parse(self.dir_name + '/experiment.xml').getroot()
        self.loadmodule_table = root.iter('LoadModuleTable').next()
        self.file_table = root.iter('FileTable').next()
        self.procedure_table = root.iter('ProcedureTable').next()
        self.metricdb_table = root.iter('MetricDBTable').next()
        self.callpath_profile = root.iter('SecCallPathProfileData').next()

        metricdb_files = glob.glob(self.dir_name + '/*.metric-db')
        self.num_pes = len(metricdb_files)

        with open(metricdb_files[0], "rb") as metricdb:
            tag = metricdb.read(18)
            version = metricdb.read(5)
            endian = metricdb.read(1)

            if endian == 'b':
                self.num_nodes = struct.unpack('>i', metricdb.read(4))[0]
                self.num_metrics = struct.unpack('>i', metricdb.read(4))[0]

        shape = [self.num_nodes * self.num_pes, self.num_metrics + 2]
        self.metrics = np.empty(shape)

        self.load_modules = {}
        self.src_files = {}
        self.procedure_names = {}
        self.metric_names = {}

        self.node_dicts = []

    def fill_tables(self):
        # create dicts of load modules, src_files and procedure_names
        for loadm in (self.loadmodule_table).iter('LoadModule'):
            self.load_modules[loadm.get('i')] = loadm.get('n')

        for filename in (self.file_table).iter('File'):
            self.src_files[filename.get('i')] = filename.get('n')

        for procedure in (self.procedure_table).iter('Procedure'):
            self.procedure_names[procedure.get('i')] = procedure.get('n')

        for metric in (self.metricdb_table).iter('MetricDB'):
            self.metric_names[metric.get('i')] = metric.get('n')

        return self.load_modules, self.src_files, self.procedure_names, self.metric_names

    def read_metricdb(self):
        metricdb_files = glob.glob(self.dir_name + '/*.metric-db')

        # assumes that glob returns a sorted order
        for pe, filename in enumerate(metricdb_files):
            with open(filename, "rb") as metricdb:
                metricdb.seek(32)
                arr1d = np.fromfile(metricdb, dtype=np.dtype('>f8'),
                                    count=self.num_nodes * self.num_metrics)

            pe_offset = pe * self.num_nodes

            self.metrics[pe_offset:pe_offset + self.num_nodes, :2].flat = arr1d.flat
            self.metrics[pe_offset:pe_offset + self.num_nodes, 2] = range(1, self.num_nodes+1)
            self.metrics[pe_offset:pe_offset + self.num_nodes, 3] = float(pe)

        df_columnns = self.metric_names.values() + ['nid', 'rank']
        self.df_metrics = pd.DataFrame(self.metrics, columns=df_columnns)

    def create_graph(self):
        # time1 = time.time()
        self.fill_tables()
        # time2 = time.time()
        # print("fill tables ", time2-time1)
        self.read_metricdb()
        # time3 = time.time()
        # print("read metric db", time3-time2)

        # lists to create a dataframe
        self.list_indices = []
        self.list_dicts = []

        # parse the ElementTree to generate a calling context tree
        root = self.callpath_profile.findall('PF')[0]
        nid = int(root.get('i'))

        node_callpath = []
        node_callpath.append(self.procedure_names[root.get('n')])
        graph_root = Node(tuple(node_callpath), None)
        node_dict = self.create_node_dict(nid, graph_root,
            self.procedure_names[root.get('n')], 'PF',
            self.src_files[root.get('f')], root.get('l'),
            self.load_modules[root.get('lm')])

        self.node_dicts.append(node_dict)

        # start graph construction at the root
        # time4 = time.time()
        self.parse_xml_children(root, graph_root, list(node_callpath))
        self.df_nodes = pd.DataFrame.from_dict(data=self.node_dicts)
        # time5 = time.time()
        # print("graph construction", time5-time4)

        dataframe = pd.merge(self.df_metrics, self.df_nodes, on='nid')
        indices = ['node', 'rank']
        dataframe.set_index(indices, drop=False, inplace=True)
        # time6 = time.time()
        # print("data frame", time6-time5)

        graph = Graph([graph_root])
        return graph, dataframe

    def parse_xml_children(self, xml_node, hnode, parent_callpath):
        """ Parses all children of an XML node.
        """
        for xml_child in xml_node.getchildren():
            if xml_child.tag != 'M':
                self.parse_xml_node(xml_child, hnode, parent_callpath)

    def parse_xml_node(self, xml_node, hparent, parent_callpath):
        """ Parses an XML node and its children recursively.
        """
        nid = int(xml_node.get('i'))

        global src_file
        global stmt_num
        xml_tag = xml_node.tag

        if xml_tag == 'PF' or xml_tag == 'Pr':
            name = self.procedure_names[xml_node.get('n')]
            src_file = xml_node.get('f')

            node_callpath = parent_callpath
            node_callpath.append(self.procedure_names[xml_node.get('n')])
            hnode = Node(tuple(node_callpath), hparent)
            node_dict = self.create_node_dict(nid, hnode,
                name, xml_tag, self.src_files[src_file], xml_node.get('l'),
                self.load_modules[xml_node.get('lm')])

        elif xml_tag == 'L':
            src_file = xml_node.get('f')
            line = xml_node.get('l')
            name = 'Loop@' + (self.src_files[src_file]).rpartition('/')[2] + ':' + line

            node_callpath = parent_callpath
            node_callpath.append(name)
            hnode = Node(tuple(node_callpath), hparent)
            node_dict = self.create_node_dict(nid, hnode,
                name, xml_tag, self.src_files[src_file], line, None)

        elif xml_tag == 'S':
            line = xml_node.get('l')
            name = 'Stmt' + str(stmt_num) + '@' + (self.src_files[src_file]).rpartition('/')[2] + ':' + line
            stmt_num = stmt_num + 1

            node_callpath = parent_callpath
            node_callpath.append(name)
            hnode = Node(tuple(node_callpath), hparent)
            node_dict = self.create_node_dict(nid, hnode,
                name, xml_tag, self.src_files[src_file], line, None)

        if xml_tag == 'C' or (xml_tag == 'Pr' and
                              self.procedure_names[xml_node.get('n')] == ''):
            # do not add a node to the graph
            self.parse_xml_children(xml_node, hparent, parent_callpath)
        else:
            self.node_dicts.append(node_dict)
            hparent.add_child(hnode)
            self.parse_xml_children(xml_node, hnode, list(node_callpath))

    def create_node_dict(self, nid, hnode, name, node_type, src_file,
            line, module):
        node_dict = {'nid': nid, 'name': name, 'type': node_type, 'file': src_file, 'line': line, 'module': module, 'node': hnode}

        return node_dict