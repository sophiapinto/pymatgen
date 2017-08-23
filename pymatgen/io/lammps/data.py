# coding: utf-8
# Copyright (c) Pymatgen Development Team.
# Distributed under the terms of the MIT License.

from __future__ import division, print_function, unicode_literals, \
    absolute_import

import re
from io import open
from collections import OrderedDict

import numpy as np

from monty.json import MSONable, MontyDecoder

from pymatgen.core.structure import Molecule, Structure
from pymatgen.core.sites import PeriodicSite

"""
This module implements classes for generating/parsing Lammps data file i.e
the file that defines the system configuration(atomic positions, bonds,
angles and dihedrals) + values of various fit paramters.

Restrictions:
    The ATOMS section in the data file that defines the atomic positions
    is assumed to be in the following format(atom style = full, this is the
    superset of several other atom styles such as angle, bond, atomic, charge
    and molecular):
    atom_id, molecule_id, atom_type, charge(optional), x, y, z

    For more info, please refer to: http://lammps.sandia.gov/doc/read_data.html
"""

__author__ = 'Kiran Mathew'
__email__ = "kmathew@lbl.gov"
__credits__ = 'Brandon Wood'


# exhaustive(as far i know) list of lammps data file keywords

HEADER_KEYWORDS = {"atoms", "bonds",  "angles", "dihedrals",  "impropers",
                   "atom types", "bond types",  "angle types", "dihedral types",
                   "improper types", "extra bond per atom", "extra angle per atom",
                   "extra dihedral per atom", "extra improper per atom",
                   "extra special per atom", "ellipsoids", "lines", "triangles",
                   "bodies", "xlo xhi", "ylo yhi", "zlo zhi", "xy xz yz"}

SECTION_KEYWORDS = {"atoms", "velocities", "masses", "ellipsoids", "lines",
                    "triangles", "bodies", "bonds", "angles", "dihedrals",
                    "impropers", "pair coeffs", "pairij coeffs", "bond coeffs",
                    "angle coeffs", "dihedral coeffs", "improper coeffs",
                    "bondbond coeffs", "bondangle coeffs",
                    "middlebondtorsion coeffs", "endbondtorsion coeffs",
                    "angletorsion coeffs", "angleangletorsion coeffs",
                    "bondbond13 coeffs", "angleangle coeffs"}


class LammpsData(MSONable):
    """
    Basic Lammps data: just the atoms section

    Args:
        box_size (list): [[x_min, x_max], [y_min,y_max], [z_min,z_max]]
        atomic_masses (list): [[atom type, mass],...]
        atoms_data (list): [[atom id, mol id, atom type, charge, x, y, z ...], ... ]
        box_tilt (list): [xy, xz, yz] for non-orthogonal systems
    """

    TEMPLATE = """Data file generated by pymatgen

{natoms} atoms

{ntypes} atom types

{xlo} {xhi} xlo xhi
{ylo} {yhi} ylo yhi
{zlo} {zhi} zlo zhi
{tilt}

Masses 

{masses}

Atoms 

{atoms}
"""

    def __init__(self, box_size, atomic_masses, atoms_data, box_tilt=None):
        self.box_size = box_size
        self.natoms = len(atoms_data)
        self.natom_types = len(atomic_masses)
        self.atomic_masses = list(atomic_masses)
        self.atoms_data = atoms_data
        self.box_tilt = box_tilt

    def __str__(self):
        """
        string representation of LammpsData

        Returns:
            String representation of the data file
        """
        def list_str(l):
            return "\n".join([" ".join([str(x) for x in ad])
                              for ad in l])

        return LammpsData.TEMPLATE.format(
            natoms=self.natoms,
            ntypes=self.natom_types,
            xlo=self.box_size[0][0],
            xhi=self.box_size[0][1],
            ylo=self.box_size[1][0],
            yhi=self.box_size[1][1],
            zlo=self.box_size[2][0],
            zhi=self.box_size[2][1],
            tilt="{:.6f} {:.6f} {:.6f} xy xz yz".format(
                self.box_tilt[0], self.box_tilt[1], self.box_tilt[2]) if self.box_tilt else "",
            masses=list_str(self.atomic_masses),
            atoms=list_str(self.atoms_data)
        )

    @staticmethod
    def check_box_size(structure, box_size, translate=False):
        """
        For Molecule objects: check the box size and if necessary translate
        the molecule so that all the sites are contained within the bounding box.
        Structure objects: compute the tilt. See
            http://lammps.sandia.gov/doc/Section_howto.html#howto-12

        Args:
            structure(Structure/Molecule)
            box_size (list): [[x_min, x_max], [y_min, y_max], [z_min, z_max]]
            translate (bool): if true move the molecule to the center of the
                new box.

        Returns:
            box_size, box_tilt
        """
        box_tilt = None
        if isinstance(structure, Molecule):
            box_size = box_size or [[0, 10], [0, 10], [0, 10]]
            box_lengths_req = [
                np.max(structure.cart_coords[:, i]) - np.min(structure.cart_coords[:, i])
                for i in range(3)]
            box_lengths = [min_max[1] - min_max[0] for min_max in box_size]
            try:
                np.testing.assert_array_less(box_lengths_req, box_lengths)
            except AssertionError:
                box_size = [[0.0, np.ceil(i*1.1)] for i in box_lengths_req]
                print("Minimum required box lengths {} larger than the provided "
                      "box lengths{}. Resetting the box size to {}".format(
                    box_lengths_req, box_lengths, box_size))
                translate = True
            if translate:
                com = structure.center_of_mass
                new_com = [(side[1] + side[0]) / 2 for side in box_size]
                translate_by = np.array(new_com) - np.array(com)
                structure.translate_sites(range(len(structure)), translate_by)
        # Structure
        else:
            a, b, c = structure.lattice.abc
            m = structure.lattice.matrix.copy()
            xhi = a
            xy = np.dot(m[1], m[0] / xhi)
            yhi = np.sqrt(b**2 - xy**2)
            xz = np.dot(m[2], m[0] / xhi)
            yz = (np.dot(m[1], m[2]) - xy * xz) / yhi
            zhi = np.sqrt(c**2 - xz**2 - yz**2)
            box_size = [[0.0, xhi], [0.0, yhi], [0.0, zhi]]
            box_tilt = [xy, xz, yz]
        return box_size, box_tilt

    def write_data_file(self, filename):
        """
        write lammps data input file from the string representation
        of the data.

        Args:
            filename (string): data file name
        """
        with open(filename, 'w') as f:
            f.write(self.__str__())

    @staticmethod
    def get_basic_system_info(structure):
        """
        Return basic system info from the given structure.

        Args:
            structure (Structure/Molecule)

        Returns:
            number of atoms, number of atom types, box size, mapping
            between the atom id and corresponding atomic masses
        """
        s = structure.copy()
        if isinstance(s, Structure):
            s.remove_oxidation_states()
        natoms = len(s)
        natom_types = len(s.symbol_set)
        elements = s.composition.elements
        elements = sorted(elements, key=lambda el: el.atomic_mass)
        atomic_masses_dict = OrderedDict(
            [(el.symbol, [i + 1, float(el.data["Atomic mass"])])
             for i, el in enumerate(elements)])
        return natoms, natom_types, atomic_masses_dict

    @staticmethod
    def get_atoms_data(structure, atomic_masses_dict, set_charge=True):
        """
        return the atoms data:
            Molecule:
                atom_id, molecule tag, atom_type, charge(if present else 0), x, y, z.
                The molecule_tag is set to 1(i.e the whole structure corresponds to
                just one molecule).
                This corresponds to lammps command: "atom_style charge" or
                 "atom_style full"
            Structure:
                atom_id, atom_type, species oxidation state, x, y, z
                atom_style = atomic/charge

        Args:
            structure (Structure/Molecule)
            atomic_masses_dict (dict):
                { atom symbol : [atom_id, atomic mass], ... }
            set_charge (bool): whether or not to set the charge field in Atoms

        Returns:
            For Molecule:
                [[atom_id, molecule tag, atom_type, charge(if present), x, y, z], ... ]
            For Structure:
                [[atom_id, atom_type, charge(if present), x, y, z], ... ]
        """
        atoms_data = []
        # to comply with atom_style='molecular' and 'full'
        mol_id = 1 if isinstance(structure, Molecule) else None

        for i, site in enumerate(structure):
            atom_type = atomic_masses_dict[site.specie.symbol][0]
            line = [i + 1]
            line += [mol_id] if mol_id else []
            line.append(atom_type)
            if set_charge:
                if isinstance(site, PeriodicSite):
                    charge = getattr(site.specie, "oxi_state", 0.0)
                else:
                    charge = getattr(site, "charge", 0.0)
                line.append(charge)
            line.extend([site.x, site.y, site.z])
            atoms_data.append(line)

        return atoms_data

    @classmethod
    def from_structure(cls, input_structure, box_size=None, set_charge=True,
                       translate=True):
        """
        Set LammpsData from the given structure or molecule object. If the input
        structure is a Molecule and if it doesnt fit in the input box then the
        box size is updated based on the max and min site coordinates of the
        molecules.

        Args:
            input_structure (Molecule/Structure)
            box_size (list): [[x_min, x_max], [y_min, y_max], [z_min, z_max]]
            set_charge (bool): whether or not to set the charge field in
                Atoms. If true, the charge will be non-zero only if the
                input_structure has the "charge" site property set.
            translate (bool): if true move the molecule to the center of the
                new box(it that is required).

        Returns:
            LammpsData
        """

        box_size, box_tilt = cls.check_box_size(input_structure, box_size,
                                                translate=translate)
        natoms, natom_types, atomic_masses_dict = \
            cls.get_basic_system_info(input_structure.copy())

        atoms_data = cls.get_atoms_data(input_structure, atomic_masses_dict,
                                        set_charge=set_charge)

        return cls(box_size, atomic_masses_dict.values(), atoms_data, box_tilt=box_tilt)

    @classmethod
    def from_file(cls, data_file, atom_style="full"):
        """
        Return LammpsData object from the data file.
        Note: use this to read in data files that conform with
        atom_style = charge or atomic or full

        Args:
            data_file (string): data file name
            atom_style (string): "full" or "charge" or "atomic"

        Returns:
            LammpsData
        """
        atoms_data = []

        data = parse_data_file(data_file)
        atomic_masses = [[int(x[0]), float(x[1])] for x in data["masses"]]
        box_size = [data['x'], data['y'], data['z']]

        int_limit = 2 if atom_style == "atomic" else 3

        if "atoms" in data:
            for x in data["atoms"]:
                atoms_data.append([int(xi) for xi in x[:int_limit]] + x[int_limit:])

        box_tilt = data.get("xy-xz-yz", None)

        return cls(box_size, atomic_masses, atoms_data, box_tilt=box_tilt)

    def as_dict(self):
        d = MSONable.as_dict(self)
        if hasattr(self, "kwargs"):
            d.update(**self.kwargs)
        return d

    @classmethod
    def from_dict(cls, d):
        decoded = {k: MontyDecoder().process_decoded(v) for k, v in d.items()
                   if not k.startswith("@")}
        return cls(**decoded)


class LammpsForceFieldData(LammpsData):
    """
    Sets Lammps data input file from force field parameters. It is recommended
    that the the convenience method from_forcefield_and_topology be used to
    create the object.

    Args:
        box_size (list): [[x_min,x_max], [y_min,y_max], [z_min,z_max]]
        atomic_masses (list): [ [atom type, atomic mass], ... ]
        pair_coeffs (list): pair coefficients,
            [[unique id, sigma, epsilon ], ... ]
        bond_coeffs (list): bond coefficients,
            [[unique id, value1, value2 ], ... ]
        angle_coeffs (list): angle coefficients,
            [[unique id, value1, value2, value3 ], ... ]
        dihedral_coeffs (list): dihedral coefficients,
            [[unique id, value1, value2, value3, value4], ... ]
        improper_coeffs (list): improper dihedral coefficients,
            [[unique id, value1, value2, value3, value4], ... ]
        atoms_data (list): [[atom id, mol id, atom type, charge, x,y,z, ...], ... ]
        bonds_data (list): [[bond id, bond type, value1, value2], ... ]
        angles_data (list): [[angle id, angle type, value1, value2, value3], ... ]
        dihedrals_data (list):
            [[dihedral id, dihedral type, value1, value2, value3, value4], ... ]
        imdihedrals_data (list):
            [[improper dihedral id, improper dihedral type, value1, value2,
            value3, value4], ... ]
    """

    def __init__(self, box_size, atomic_masses, pair_coeffs, bond_coeffs,
                 angle_coeffs, dihedral_coeffs, improper_coeffs, atoms_data,
                 bonds_data, angles_data, dihedrals_data, imdihedrals_data):
        super(LammpsForceFieldData, self).__init__(box_size, atomic_masses,
                                                   atoms_data)
        # number of types
        self.nbond_types = len(bond_coeffs)
        self.nangle_types = len(angle_coeffs)
        self.ndih_types = len(dihedral_coeffs)
        self.nimdih_types = len(improper_coeffs)
        # number of parameters
        self.nbonds = len(bonds_data)
        self.nangles = len(angles_data)
        self.ndih = len(dihedrals_data)
        self.nimdihs = len(imdihedrals_data)
        # coefficients
        self.pair_coeffs = pair_coeffs
        self.bond_coeffs = bond_coeffs
        self.angle_coeffs = angle_coeffs
        self.dihedral_coeffs = dihedral_coeffs
        self.improper_coeffs = improper_coeffs
        # data
        self.bonds_data = bonds_data
        self.angles_data = angles_data
        self.dihedrals_data = dihedrals_data
        self.imdihedrals_data = imdihedrals_data

    def __str__(self):
        """
        returns a string of lammps data input file
        """
        lines = []
        # title
        lines.append("Data file generated by pymatgen\n")

        # count
        lines.append("{} atoms".format(self.natoms))
        lines.append("{} bonds".format(self.nbonds))
        lines.append("{} angles".format(self.nangles))
        if self.ndih > 0:
            lines.append("{} dihedrals".format(self.ndih))
        if self.nimdihs > 0:
            lines.append("{} impropers".format(self.nimdihs))

        # types
        lines.append("\n{} atom types".format(self.natom_types))
        lines.append("{} bond types".format(self.nbond_types))
        lines.append("{} angle types".format(self.nangle_types))
        if self.ndih > 0:
            lines.append("{} dihedral types".format(self.ndih_types))
        if self.nimdihs > 0:
            lines.append("{} improper types".format(self.nimdih_types))

        # box size
        lines.append("\n{} {} xlo xhi\n{} {} ylo yhi\n{} {} zlo zhi".format(
            self.box_size[0][0], self.box_size[0][1],
            self.box_size[1][0], self.box_size[1][1],
            self.box_size[2][0], self.box_size[2][1]))

        def set_lines_from_list(lines, block_name, input_list):
            if input_list:
                lines.append("\n" + block_name + " \n")
                for ad in input_list:
                    lines.append(" ".join([str(x) for x in ad]))

        # masses
        set_lines_from_list(lines, "Masses", self.atomic_masses)

        # coefficients
        set_lines_from_list(lines, "Pair Coeffs", self.pair_coeffs)
        set_lines_from_list(lines, "Bond Coeffs", self.bond_coeffs)
        set_lines_from_list(lines, "Angle Coeffs", self.angle_coeffs)
        if self.ndih > 0:
            set_lines_from_list(lines, "Dihedral Coeffs",
                                     self.dihedral_coeffs)
        if self.nimdihs > 0:
            set_lines_from_list(lines, "Improper Coeffs",
                                     self.improper_coeffs)

        # data
        set_lines_from_list(lines, "Atoms", self.atoms_data)
        set_lines_from_list(lines, "Bonds", self.bonds_data)
        set_lines_from_list(lines, "Angles", self.angles_data)
        if self.ndih > 0:
            set_lines_from_list(lines, "Dihedrals", self.dihedrals_data)
        if self.nimdihs > 0:
            set_lines_from_list(lines, "Impropers", self.imdihedrals_data)
        return '\n'.join(lines)

    @staticmethod
    def get_basic_system_info(molecule):
        natoms = len(molecule)
        atom_types = set(molecule.site_properties.get("ff_map", molecule.symbol_set))
        natom_types = len(atom_types)
        elements = {}
        for s in molecule:
            label = str(s.ff_map) if hasattr(molecule[0], "ff_map") else s.specie.symbol
            elements[label] = float(s.specie.atomic_mass)
        elements_items = list(elements.items())
        elements_items = sorted(elements_items, key=lambda el_item: el_item[1])
        atomic_masses_dict = OrderedDict([(el_item[0], [i + 1, el_item[1]])
                                          for i, el_item in enumerate(elements_items)])
        return natoms, natom_types, atomic_masses_dict

    @staticmethod
    def get_param_coeff(forcefield, param_name, atom_types_map=None):
        """
        get the parameter coefficients and mapping from the force field.

        Args:
            forcefield (ForceField): ForceField object
            param_name (string): name of the parameter for which
            the coefficients are to be set.
            atom_types_map (dict): maps atom type to the atom type id.
                Used to set hthe pair coeffs.
                e.g. {"C2": [3], "H2": [1], "H1": [2]}

        Returns:
            [[parameter id, value1, value2, ... ], ... ] and
            {parameter key: parameter id, ...}
        """
        if hasattr(forcefield, param_name):
            param = getattr(forcefield, param_name)
            param_coeffs = []
            param_map = {}
            if param_name == "pairs":
                for i, item in enumerate(param.items()):
                    key = item[0][0]
                    param_coeffs.append([atom_types_map[key][0]] + list(item[1]))
                param_coeffs = sorted(param_coeffs, key=lambda ii: ii[0])
            elif param:
                for i, item in enumerate(param.items()):
                    param_coeffs.append([i + 1] + list(item[1]))
                    param_map[item[0]] = i+1
            return param_coeffs, param_map
        else:
            raise AttributeError

    @staticmethod
    def get_atoms_data(mols, mols_number, molecule, atomic_masses_dict,
                       topologies, atom_to_mol=None):
        """
        Return the atoms data.

        Args:
            mols (list): list of Molecule objects.
            mols_number (list): number of each type of molecule in mols list.
            molecule (Molecule): the molecule assembled from the molecules
                in the mols list.
            topologies (list): list of Topology objects, one for each molecule
                type in mols list
            atom_to_mol (dict):  maps atom_id --> [mol_type, mol_id,
                local atom id in the mol with id mol_id]

        Returns:
            atoms_data: [[atom id, mol type, atom type, charge, x, y, z], ... ]
            molid_to_atomid: [ [global atom id 1, id 2, ..], ...], the
                index will be the global mol id
        """
        atoms_data = []
        molid_to_atomid = []
        nmols = len(mols)
        # set up map atom_to_mol:
        #   atom_id --> [mol_type, mol_id, local atom id in the mol with id mol id]
        # set up map molid_to_atomid:
        #   gobal molecule id --> [[atom_id1, atom_id2,...], ...]
        # This assumes that the atomic order in the assembled molecule can be
        # obtained from the atomic order in the constituent molecules.
        if not atom_to_mol:
            atom_to_mol = {}
            molid_to_atomid = []
            shift_ = 0
            mol_id = 0
            for mol_type in range(nmols):
                natoms = len(mols[mol_type])
                for num_mol_id in range(mols_number[mol_type]):
                    tmp = []
                    for mol_atom_id in range(natoms):
                        atom_id = num_mol_id * natoms + mol_atom_id + shift_
                        atom_to_mol[atom_id] = [mol_type, mol_id, mol_atom_id]
                        tmp.append(atom_id)
                    mol_id += 1
                    molid_to_atomid.append(tmp)
                shift_ += len(mols[mol_type]) * mols_number[mol_type]
        # set atoms data from the molecule assembly consisting of
        # molecules from mols list with their count from mol_number list.
        # atom id, mol id, atom type, charge from topology, x, y, z
        for i, site in enumerate(molecule):
            label = str(site.ff_map) if hasattr(site, "ff_map") else site.specie.symbol
            atom_type = atomic_masses_dict[label][0]
            # atom_type = molecule.symbol_set.index(site.species_string) + 1
            atom_id = i + 1
            mol_type = atom_to_mol[i][0] + 1
            mol_id = atom_to_mol[i][1] + 1
            mol_atom_id = atom_to_mol[i][2] + 1
            charge = 0.0
            if hasattr(topologies[0], "charges"):
                if topologies[mol_type - 1].charges:
                    charge = topologies[mol_type - 1].charges[mol_atom_id - 1]
            atoms_data.append([atom_id, mol_id, atom_type, charge,
                               site.x, site.y, site.z])
        return atoms_data, molid_to_atomid

    @staticmethod
    def get_param_data(param_name, param_map, mols, mols_number, topologies,
                       molid_to_atomid):
        """
        set the data for the parameter named param_name from the topology.

        Args:
            param_name (string): parameter name, example: "bonds"
            param_map (dict):
                { mol_type: {parameter_key : unique parameter id, ... }, ... }
                example: {0: {("c1","c2"): 1}} ==> c1-c2 bond in mol_type=0
                    has the global id of 1
            mols (list): list of molecules.
            mols_number (list): number of each type of molecule in mols list.
            topologies (list): list of Topology objects, one for each molecule
                type in mols list
            molid_to_atomid (list): [ [gloabal atom id 1, id 2, ..], ...],
                the index is the global mol id

        Returns:
            [ [parameter id, parameter type, global atom id1, global atom id2, ...], ... ]
        """
        param_data = []
        if hasattr(topologies[0], param_name) and getattr(topologies[0], param_name):
            nmols = len(mols)
            mol_id = 0
            skip = 0
            shift_ = 0
            # set the parameter data using the topology info
            # example: loop over all bonds in the system
            # mol_id --> global molecule id
            # mol_type --> type of molecule
            # mol_param_id --> local parameter id in that molecule
            for mol_type in range(nmols):
                param_obj = getattr(topologies[mol_type], param_name)
                nparams = len(param_obj)
                for num_mol_id in range(mols_number[mol_type]):
                    for mol_param_id in range(nparams):
                        param_id = num_mol_id * nparams + mol_param_id + shift_
                        # example: get the bonds list for mol_type molecule
                        param_obj = getattr(topologies[mol_type], param_name)
                        # connectivity info(local atom ids and type) for the
                        # parameter with the local id 'mol_param_id'.
                        # example: single bond = [i, j, bond_type]
                        param = param_obj[mol_param_id]
                        param_atomids = []
                        # loop over local atom ids that constitute the parameter
                        # for the molecule type, mol_type
                        # example: single bond = [i,j,bond_label]
                        for atomid in param[:-1]:
                            # local atom id to global atom id
                            global_atom_id = molid_to_atomid[mol_id][atomid]
                            param_atomids.append(global_atom_id + 1)
                        param_type = tuple(param[-1])
                        param_type_reversed = tuple(reversed(param_type))
                        # example: get the unique number id for the bond_type
                        if param_type in param_map:
                            key = param_type
                        elif param_type_reversed in param_map:
                            key = param_type_reversed
                        else:
                            key = None
                        if key:
                            param_type_id = param_map[key]
                            param_data.append(
                                [param_id + 1 - skip, param_type_id] + param_atomids)
                        else:
                            skip += 1
                            print("{} or {} Not available".format(param_type,
                                                                  param_type_reversed))
                    mol_id += 1
                shift_ += nparams * mols_number[mol_type]
        return param_data

    @staticmethod
    def from_forcefield_and_topology(mols, mols_number, box_size, molecule,
                                     forcefield, topologies):
        """
        Return LammpsForceFieldData object from force field and topology info
        for the 'molecule' assembled from the constituent molecules specified
        in the 'mols' list with their count specified in the 'mols_number' list.

        Args:
            mols (list): List of Molecule objects
            mols_number (list): List of number of molecules of each
                molecule type in mols
            box_size (list): [[x_min,x_max], [y_min,y_max], [z_min,z_max]]
            molecule (Molecule): The molecule that is assembled from mols
                and mols_number
            forcefield (ForceFiled): Force filed information
            topologies (list): List of Topology objects, one for each
                molecule type in mols.

        Returns:
            LammpsForceFieldData
        """

        natoms, natom_types, atomic_masses_dict = \
            LammpsForceFieldData.get_basic_system_info(molecule.copy())

        box_size, _ = LammpsForceFieldData.check_box_size(molecule, box_size)

        # set the coefficients and map from the force field

        # bonds
        bond_coeffs, bond_map = \
            LammpsForceFieldData.get_param_coeff(forcefield, "bonds")

        # angles
        angle_coeffs, angle_map = \
            LammpsForceFieldData.get_param_coeff(forcefield, "angles")

        # pair coefficients
        pair_coeffs, _ = \
            LammpsForceFieldData.get_param_coeff(forcefield, "pairs",
                                                 atomic_masses_dict)

        # dihedrals
        dihedral_coeffs, dihedral_map = \
            LammpsForceFieldData.get_param_coeff(forcefield, "dihedrals")

        # improper dihedrals
        improper_coeffs, imdihedral_map = \
            LammpsForceFieldData.get_param_coeff(forcefield, "imdihedrals")

        # atoms data. topology used for setting charge if present
        atoms_data, molid_to_atomid = LammpsForceFieldData.get_atoms_data(
            mols, mols_number, molecule, atomic_masses_dict, topologies)

        # set the other data from the molecular topologies

        # bonds
        bonds_data = LammpsForceFieldData.get_param_data(
            "bonds", bond_map, mols, mols_number, topologies, molid_to_atomid)

        # angles
        angles_data = LammpsForceFieldData.get_param_data(
            "angles", angle_map, mols, mols_number, topologies, molid_to_atomid)

        # dihedrals
        dihedrals_data = LammpsForceFieldData.get_param_data(
            "dihedrals", dihedral_map, mols, mols_number, topologies,
            molid_to_atomid)

        # improper dihedrals
        imdihedrals_data = LammpsForceFieldData.get_param_data(
            "imdihedrals", imdihedral_map, mols, mols_number, topologies,
            molid_to_atomid)

        return LammpsForceFieldData(box_size, atomic_masses_dict.values(),
                                    pair_coeffs, bond_coeffs,
                                    angle_coeffs, dihedral_coeffs,
                                    improper_coeffs, atoms_data,
                                    bonds_data, angles_data, dihedrals_data,
                                    imdihedrals_data)

    @staticmethod
    def _get_coeffs(data, name):
        val = []
        if name in data:
            for x in data[name]:
                val.append([int(x[0])] + x[1:])
        return val

    @staticmethod
    def _get_non_atoms_data(data, name):
        val = []
        if name in data:
            for x in data[name]:
                val.append([int(xi) for xi in x])
        return val

    @classmethod
    def from_file(cls, data_file):
        """
        Return LammpsForceFieldData object from the data file. It is assumed
        that the forcefield paramter sections for pairs, bonds, angles,
        dihedrals and improper dihedrals are named as follows(not case sensitive):
        "Pair Coeffs", "Bond Coeffs", "Angle Coeffs", "Dihedral Coeffs" and
        "Improper Coeffs". For "Pair Coeffs", values for factorial(n_atom_types)
        pairs must be specified.

        Args:
            data_file (string): the data file name

        Returns:
            LammpsForceFieldData
        """
        atoms_data = []
        data = parse_data_file(data_file)
        atomic_masses = [[int(x[0]), float(x[1])] for x in data["masses"]]
        box_size = [data['x'], data['y'], data['z']]

        pair_coeffs = cls._get_coeffs(data, "pair-coeffs")
        bond_coeffs = cls._get_coeffs(data, "bond-coeffs")
        angle_coeffs = cls._get_coeffs(data, "angle-coeffs")
        dihedral_coeffs = cls._get_coeffs(data, "dihedral-coeffs")
        improper_coeffs = cls._get_coeffs(data, "improper-coeffs")

        if "atoms" in data:
            for x in data["atoms"]:
                atoms_data.append([int(xi) for xi in x[:3]] + x[3:])

        bonds_data = cls._get_non_atoms_data(data, "bonds")
        angles_data = cls._get_non_atoms_data(data, "angles")
        dihedral_data = cls._get_non_atoms_data(data, "dihedrals")
        imdihedral_data = cls._get_non_atoms_data(data, "impropers")

        return cls(box_size, atomic_masses, pair_coeffs,
                    bond_coeffs, angle_coeffs,
                    dihedral_coeffs, improper_coeffs,
                    atoms_data, bonds_data, angles_data,
                    dihedral_data, imdihedral_data)


def parse_data_file(filename):
    """
    A very general parser for arbitrary lammps data files.

    Args:
        filename (str): path to the data file

    Returns:
        dict
    """
    data = {}
    count_pattern = re.compile(r'^\s*(\d+)\s+([a-zA-Z]+)$')
    types_pattern = re.compile(r'^\s*(\d+)\s+([a-zA-Z]+)\s+types$')
    box_pattern = re.compile(r'^\s*([0-9eE\.+-]+)\s+([0-9eE\.+-]+)\s+([xyz])lo\s+([xyz])hi$')
    tilt_pattern = re.compile(r'^\s*([0-9eE\.+-]+)\s+([0-9eE\.+-]+)\s+([0-9eE\.+-]+)\s+xy\s+xz\s+yz$')
    key = None
    with open(filename) as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                if line.lower() in SECTION_KEYWORDS:
                    key = line.lower()
                    key = key.replace(" ", "-")
                    data[key] = []
                elif key and key in data:
                    data[key].append([float(x) for x in line.split()])
                else:
                    if types_pattern.search(line):
                        m = types_pattern.search(line)
                        data["{}-types".format(m.group(2))] = int(m.group(1))
                    elif box_pattern.search(line):
                        m = box_pattern.search(line)
                        data[m.group(3)] = [float(m.group(1)), float(m.group(2))]
                    elif count_pattern.search(line):
                        tokens = line.split(" ", 1)
                        data["n{}".format(tokens[-1])] = int(tokens[0])
                    elif tilt_pattern.search(line):
                        m = tilt_pattern.search(line)
                        data["xy-xz-yz"] = [float(m.group(1)),
                                            float(m.group(2)),
                                            float(m.group(3))]
    return data


