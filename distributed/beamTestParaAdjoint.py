from __future__ import division
import numpy as np
from openmdao.api import ExplicitComponent, ImplicitComponent,Problem, Group, IndepVarComp,ScipyOptimizeDriver
from mpi4py import MPI
from six.moves import range
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu
from petsc4py import PETSc


from contextlib import contextmanager
import time
@contextmanager
def timeblock ( label ):
    start = time.clock ()
    try:
        yield
    finally:
        end = time.clock ()
        print ('{}:{} '. format (label , end - start ))

class MomentOfInertiaComp(ExplicitComponent):

    def initialize(self):
        self.options.declare('num_elements', types=int)
        self.options.declare('b')

    def setup(self):
        num_elements = self.options['num_elements']

        self.add_input('h', shape=num_elements)
        self.add_output('I', shape=num_elements)

        rows = np.arange(num_elements)
        cols = np.arange(num_elements)
        self.declare_partials('I', 'h', rows=rows, cols=cols)

    def compute(self, inputs, outputs):
        b = self.options['b']

        outputs['I'] = 1./12. * b * inputs['h'] ** 3

    def compute_partials(self, inputs, partials):
        b = self.options['b']

        partials['I', 'h'] = 1./4. * b * inputs['h'] ** 2



class LocalStiffnessMatrixComp(ExplicitComponent):

    def initialize(self):
        self.options.declare('num_elements', types=int)
        self.options.declare('E')
        self.options.declare('L')

    def setup(self):
        num_elements = self.options['num_elements']
        E = self.options['E']
        L = self.options['L']

        self.add_input('I', shape=num_elements)
        self.add_output('K_local', shape=(num_elements, 4, 4))

        L0 = L / num_elements
        coeffs = np.empty((4, 4))
        coeffs[0, :] = [12, 6 * L0, -12, 6 * L0]
        coeffs[1, :] = [6 * L0, 4 * L0 ** 2, -6 * L0, 2 * L0 ** 2]
        coeffs[2, :] = [-12, -6 * L0, 12, -6 * L0]
        coeffs[3, :] = [6 * L0, 2 * L0 ** 2, -6 * L0, 4 * L0 ** 2]
        coeffs *= E / L0 ** 3

        self.mtx = mtx = np.zeros((num_elements, 4, 4, num_elements))
        for ind in range(num_elements):
            self.mtx[ind, :, :, ind] = coeffs

        self.declare_partials('K_local', 'I',
            val=self.mtx.reshape(16 * num_elements, num_elements))

    def compute(self, inputs, outputs):
        num_elements = self.options['num_elements']

        outputs['K_local'] = 0
        for ind in range(num_elements):
            outputs['K_local'][ind, :, :] = self.mtx[ind, :, :, ind] * inputs['I'][ind]

class StatesComp(ImplicitComponent):

    def initialize(self):
        self.options.declare('num_elements', types=int)
        self.options.declare('force_vector', types=np.ndarray)

    def setup(self):
        self.run_once = 0
        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1
        size = 2 * num_nodes + 2

        self.add_input('K_local', shape=(num_elements, 4, 4))
        self.add_output('d', shape=size)

        cols = np.arange(16*num_elements)
        rows = np.repeat(np.arange(4), 4)
        rows = np.tile(rows, num_elements) + np.repeat(np.arange(num_elements), 16) * 2

        self.declare_partials('d', 'K_local', rows=rows, cols=cols)
        self.declare_partials('d', 'd')

    def solve_nonlinear(self, inputs, outputs):
        # self.K = self.assemble_CSC_K(inputs)
        #from scipy.io import savemat
        #savemat('K', {'K': self.K})
        rank = PETSc.COMM_WORLD.rank

        MPI.COMM_WORLD.Barrier
        if self.run_once == 0:
            self.run_once += 1
        else:
            return None

        MPI.COMM_WORLD.Barrier
        rank = MPI.COMM_WORLD.rank
        if rank == 0:
            print("solving linear system")

        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1
        size = 2 * num_nodes + 2
        A = PETSc.Mat()
        A.create(comm=MPI.COMM_WORLD)
        A.setSizes([size, size])
        A.setType("aij")
        A.setUp()
        rows,cols,data = self.assemble_struct_K(inputs)
        #np.save('rows',rows)
        #np.save('cols',cols)
        #np.save('data',data)
        for idx,(row,col) in enumerate(zip(rows,cols)):
            A.setValue(row,col, data[idx])
        A.setValue(size-2,size-2,0)
        A.setValue(size-1, size-1, 0)
        A.assemble()
        x, f = A.createVecs()
        x.set(0)
        f.set(0)
        f.setValue(size-4,-1)
        f.assemble()
        ksp = PETSc.KSP()
        ksp.create(MPI.COMM_WORLD)
        ksp.setOperators(A)
        ksp.setFromOptions()
        MPI.COMM_WORLD.Barrier
        ksp.solve(f, x)
        MPI.COMM_WORLD.Barrier
        #res = ksp.getResidualNorm()
        local_x = np.zeros(size)
        id_start, id_end = A.getOwnershipRange()
        # print("processors %d"%rank," owns %d,%d"%(id_start,id_end))
        local_x[id_start:id_end] = x[...]
        global_x = np.zeros(size)
        #print(np.dot(A[:, :], global_x)-f[:])
        # print(np.array(x))
        MPI.COMM_WORLD.Reduce([local_x, MPI.DOUBLE], [global_x, MPI.DOUBLE], op=MPI.SUM)
        outputs['d'] = global_x
        if rank == 0:
            print(self.run_once)

    def linearize(self, inputs, outputs, jacobian):
        num_elements = self.options['num_elements']
        print('computing partials')

        self.K = self.assemble_CSC_K(inputs)

        i_elem = np.tile(np.arange(4), 4)
        i_d = np.tile(i_elem, num_elements) + np.repeat(np.arange(num_elements), 16) * 2

        jacobian['d', 'K_local'] = outputs['d'][i_d]

        jacobian['d', 'd'] = self.K.toarray()

    def assemble_CSC_K(self, inputs):

        """
        Assemble the stiffness matrix in sparse CSC format.

        Returns
        -------
        ndarray
            Stiffness matrix as dense ndarray.
        """
        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1
        num_entry = num_elements * 12 + 4
        ndim = num_entry + 4

        data = np.zeros((ndim, ))
        cols = np.empty((ndim, ))
        rows = np.empty((ndim, ))

        # First element.
        data[:16] = inputs['K_local'][0, :, :].flat
        cols[:16] = np.tile(np.arange(4), 4)
        rows[:16] = np.repeat(np.arange(4), 4)

        j = 16
        for ind in range(1, num_elements):
            ind1 = 2 * ind
            K = inputs['K_local'][ind, :, :]

            # NW quadrant gets summed with previous connected element.
            data[j-6:j-4] += K[0, :2]
            data[j-2:j] += K[1, :2]

            # NE quadrant
            data[j:j+4] = K[:2, 2:].flat
            rows[j:j+4] = np.array([ind1, ind1, ind1 + 1, ind1 + 1])
            cols[j:j+4] = np.array([ind1 + 2, ind1 + 3, ind1 + 2, ind1 + 3])

            # SE and SW quadrants together
            data[j+4:j+12] = K[2:, :].flat
            rows[j+4:j+12] = np.repeat(np.arange(ind1 + 2, ind1 + 4), 4)
            cols[j+4:j+12] = np.tile(np.arange(ind1, ind1 + 4), 2)

            j += 12

        data[-4:] = 1.0
        rows[-4] = 2 * num_nodes
        rows[-3] = 2 * num_nodes + 1
        rows[-2] = 0.0
        rows[-1] = 1.0
        cols[-4] = 0.0
        cols[-3] = 1.0
        cols[-2] = 2 * num_nodes
        cols[-1] = 2 * num_nodes + 1

        n_K = 2 * num_nodes + 2
        return coo_matrix((data, (rows, cols)), shape=(n_K, n_K)).tocsc()

    def assemble_struct_K(self, inputs):
        """
        Assemble the stiffness matrix in sparse CSC format.

        Returns
        -------
        ndarray
            Stiffness matrix as dense ndarray.
        """
        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1
        num_entry = num_elements * 12 + 4
        ndim = num_entry + 4

        data = np.zeros((ndim, ))
        cols = np.empty((ndim, ))
        rows = np.empty((ndim, ))

        # First element.
        data[:16] = inputs['K_local'][0, :, :].flat
        cols[:16] = np.tile(np.arange(4), 4)
        rows[:16] = np.repeat(np.arange(4), 4)

        j = 16
        for ind in range(1, num_elements):
            ind1 = 2 * ind
            K = inputs['K_local'][ind, :, :]

            # NW quadrant gets summed with previous connected element.
            data[j-6:j-4] += K[0, :2]
            data[j-2:j] += K[1, :2]

            # NE quadrant
            data[j:j+4] = K[:2, 2:].flat
            rows[j:j+4] = np.array([ind1, ind1, ind1 + 1, ind1 + 1])
            cols[j:j+4] = np.array([ind1 + 2, ind1 + 3, ind1 + 2, ind1 + 3])

            # SE and SW quadrants together
            data[j+4:j+12] = K[2:, :].flat
            rows[j+4:j+12] = np.repeat(np.arange(ind1 + 2, ind1 + 4), 4)
            cols[j+4:j+12] = np.tile(np.arange(ind1, ind1 + 4), 2)

            j += 12

        data[-4:] = 1.0
        rows[-4] = 2 * num_nodes
        rows[-3] = 2 * num_nodes + 1
        rows[-2] = 0.0
        rows[-1] = 1.0
        cols[-4] = 0.0
        cols[-3] = 1.0
        cols[-2] = 2 * num_nodes
        cols[-1] = 2 * num_nodes + 1

        return rows, cols, data
"""
    def solve_linear(self, d_outputs, d_residuals, mode):
        print("solve_linear")
        self.lu = splu(self.K)
        if mode == 'fwd':
            d_outputs['d'] = self.lu.solve(d_residuals['d'])
        else:
            d_residuals['d'] = self.lu.solve(d_outputs['d'])
"""
class DisplacementsComp(ExplicitComponent):

    def initialize(self):
        self.options.declare('num_elements', types=int)

    def setup(self):
        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1
        size = 2 * num_nodes + 2

        self.add_input('d', shape=size)
        self.add_output('displacements', shape=2 * num_nodes)

        arange = np.arange(2 * num_nodes)
        self.declare_partials('displacements', 'd', val=1., rows=arange, cols=arange)

    def compute(self, inputs, outputs):
        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1

        outputs['displacements'] = inputs['d'][:2 * num_nodes]


class ComplianceComp(ExplicitComponent):

    def initialize(self):
        self.options.declare('num_elements', types=int)
        self.options.declare('force_vector', types=np.ndarray)

    def setup(self):
        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1
        force_vector = self.options['force_vector']

        self.add_input('displacements', shape=2 * num_nodes)
        self.add_output('compliance')

        self.declare_partials('compliance', 'displacements',
                              val=force_vector.reshape((1, 2 * num_nodes)))

    def compute(self, inputs, outputs):
        force_vector = self.options['force_vector']

        outputs['compliance'] = np.dot(force_vector, inputs['displacements'])



class VolumeComp(ExplicitComponent):

    def initialize(self):
        self.options.declare('num_elements', types=int)
        self.options.declare('b', default=1.)
        self.options.declare('L')

    def setup(self):
        num_elements = self.options['num_elements']
        b = self.options['b']
        L = self.options['L']
        L0 = L / num_elements

        self.add_input('h', shape=num_elements)
        self.add_output('volume')

        self.declare_partials('volume', 'h', val=b * L0)

    def compute(self, inputs, outputs):
        num_elements = self.options['num_elements']
        b = self.options['b']
        L = self.options['L']
        L0 = L / num_elements

        outputs['volume'] = np.sum(inputs['h'] * b * L0)



class BeamGroup(Group):

    def initialize(self):
        self.options.declare('E')
        self.options.declare('L')
        self.options.declare('b')
        self.options.declare('volume')
        self.options.declare('num_elements', int)

    def setup(self):
        E = self.options['E']
        L = self.options['L']
        b = self.options['b']
        volume = self.options['volume']
        num_elements = self.options['num_elements']
        num_nodes = num_elements + 1

        force_vector = np.zeros(2 * num_nodes)
        force_vector[-2] = -1.

        inputs_comp = IndepVarComp()
        inputs_comp.add_output('h', shape=num_elements)
        self.add_subsystem('inputs_comp', inputs_comp)

        I_comp = MomentOfInertiaComp(num_elements=num_elements, b=b)
        self.add_subsystem('I_comp', I_comp)

        comp = LocalStiffnessMatrixComp(num_elements=num_elements, E=E, L=L)
        self.add_subsystem('local_stiffness_matrix_comp', comp)

        comp = StatesComp(num_elements=num_elements, force_vector=force_vector)
        self.add_subsystem('states_comp', comp)

        comp = DisplacementsComp(num_elements=num_elements)
        self.add_subsystem('displacements_comp', comp)

        comp = ComplianceComp(num_elements=num_elements, force_vector=force_vector)
        self.add_subsystem('compliance_comp', comp)

        comp = VolumeComp(num_elements=num_elements, b=b, L=L)
        self.add_subsystem('volume_comp', comp)

        self.connect('inputs_comp.h', 'I_comp.h')
        self.connect('I_comp.I', 'local_stiffness_matrix_comp.I')
        self.connect(
            'local_stiffness_matrix_comp.K_local',
            'states_comp.K_local')
        self.connect(
            'states_comp.d',
            'displacements_comp.d')
        self.connect(
            'displacements_comp.displacements',
            'compliance_comp.displacements')
        self.connect(
            'inputs_comp.h',
            'volume_comp.h')

        self.add_design_var('inputs_comp.h', lower=1e-2, upper=10.)
        self.add_objective('compliance_comp.compliance')
        self.add_constraint('volume_comp.volume', equals=volume)


E = 7E9
L = 1.
b = 0.1
volume = 0.01

num_elements = 10000
prob = Problem(model=BeamGroup(E=E, L=L, b=b, volume=volume, num_elements=num_elements))

prob.driver = ScipyOptimizeDriver()
prob.driver.options['optimizer'] = 'SLSQP'
prob.driver.options['tol'] = 1e-9
prob.driver.options['disp'] = False
prob.driver.options['maxiter'] = 0
prob.setup()
with timeblock('parallel linear solver and adjoint method with %d processors costs: '%MPI.COMM_WORLD.size):
    prob.run_driver()


"""
for num_elements in [10,100,200,500,1000,2000]:
    prob = Problem(model=BeamGroup(E=E, L=L, b=b, volume=volume, num_elements=num_elements))


    prob.setup()
    with timeblock('direct time cost with ' + str(num_elements)+ " elements: "):
        prob.compute_totals(of=['compliance_comp.compliance'], wrt=['inputs_comp.h'])
"""