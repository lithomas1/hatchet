##############################################################################
# Copyright (c) 2017-2019, Lawrence Livermore National Security, LLC.
# Produced at the Lawrence Livermore National Laboratory.
#
# This file is part of Hatchet.
# Created by Abhinav Bhatele <bhatele@llnl.gov>.
# LLNL-CODE-741008. All rights reserved.
#
# For details, see: https://github.com/LLNL/hatchet
# Please also read the LICENSE file for the MIT License notice.
##############################################################################
import re
import pandas as pd

from .node import Node
from .graph import Graph
from .util.timer import Timer


class GprofDotReader:
    """ Read in gprof/callgrind output in dot format generated by gprof2dot.
    """

    def __init__(self, filename):
        self.dotfile = filename

        self.name_to_hnode = {}
        self.name_to_dict = {}

        self.timer = Timer()
 

    def create_graph(self):
        """ Read the DOT files to create a graph.
        """
        idx = 0
       
        with open(self.dotfile) as stream:
            for line in stream:
                match_edge = re.match(r'^\s[\"]?([\w\(\ \)@\.\']+)[\"]?\s->\s[\"]?([\w\(\ \)@\.\']+)[\"]?\s\[.*label=\"(.*)\xc3\x97\".*\];', line)
                # found an edge:
                if match_edge:
                    src_name = match_edge.group(1)
                    dst_name = match_edge.group(2)
                    edge_details = match_edge.group(3).split('\\n')

                    if src_name not in self.name_to_hnode.keys():
                        # create a node if it doesn't exist yet
                        src_hnode = Node((src_name,), None)
                        self.name_to_hnode[src_name] = src_hnode
                    else:
                        # else retrieve node from dict
                        src_hnode = self.name_to_hnode[src_name]

                    if dst_name not in self.name_to_hnode.keys():
                        # create a node if it doesn't exist yet
                        dst_hnode = Node((dst_name,), src_hnode)
                        self.name_to_hnode[dst_name] = dst_hnode
                    else:
                        # else retrieve node from dict and add source node
                        # as parent
                        dst_hnode = self.name_to_hnode[dst_name]
                        dst_hnode.add_parent(src_hnode)

                    # add destination node as child
                    src_hnode.add_child(dst_hnode)

                else:
                    match_node = re.match(r'^\s[\"]?([\w\(\ \)@\.\']+)[\"]?\s\[.*label=\"(.*)\xc3\x97\".*\];', line)
                    # found a node
                    if match_node:
                        node_name = match_node.group(1)
                        node_details = match_node.group(2).split('\\n')

                        if node_name not in self.name_to_hnode.keys():
                            # create a node if it doesn't exist yet
                            hnode = Node((node_name,), None)
                            self.name_to_hnode[node_name] = hnode
                        else:
                            hnode = self.name_to_hnode[node_name]

                        # create a dict with node properties
                        inc_time = float(re.match('(.*)\%', node_details[2]).group(1))
                        exc_time = float(re.match('\((.*)\%\)', node_details[3]).group(1))
                        node_dict = {'module': node_details[0], 'name': node_name, 'inc-time': inc_time, 'exc-time': exc_time, 'node': hnode}
                        self.name_to_dict[node_name] = node_dict

        # add all nodes with no parents to the list of roots
        list_roots = []
        for (key, val) in self.name_to_hnode.items():
            if not val.parents:
                list_roots.append(val)

        # correct callpaths of all nodes
        for root in list_roots:
            for node in root.traverse():
                if node.parents:
                    parent_callpath = node.parents[0].callpath
                    node_callpath =  parent_callpath + node.callpath
                    node.set_callpath(node_callpath)

        return list_roots


    def create_graphframe(self):
        """ Read the DOT file generated by gprof2dot to create a graphframe.
            The DOT file contains a call graph.
        """
        with self.timer.phase('graph construction'):
            roots = self.create_graph()
            graph = Graph(roots)

        with self.timer.phase('data frame'):
            dataframe = pd.DataFrame.from_dict(data=self.name_to_dict.values())
            index = ['node']
            dataframe.set_index(index, drop=False, inplace=True)

        return graph, dataframe
