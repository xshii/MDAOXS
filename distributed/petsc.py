from petsc4py import PETSc
from mpi4py import MPI
import numpy as np
import scipy.io as si
rank = PETSc.COMM_WORLD.rank
num_ranks = PETSc.COMM_WORLD.size
rows = np.load('rows.npy')
cols = np.load('cols.npy')
data = np.load('data.npy')
#Amat = si.loadmat('A.mat')['A']

N= 104
A = PETSc.Mat()
A.create(comm=MPI.COMM_WORLD)
A.setSizes([N, N])
A.setType("aij")
A.setUp()

id_start, id_end = A.getOwnershipRange()


#A.setValues(list(np.arange(N)),list(np.arange(N)),Amat)

#for i in range(0,N):
#    A.setValue(i,i,i+1)

for idx, (row, col) in enumerate(zip(rows, cols)):
    A.setValue(row, col, data[idx])
A.setValue(N-2,N-2,0)
A.setValue(N-1,N-1,0)
A.assemblyBegin()
A.assemblyEnd()

x,c = A.createVecs()
x.set(0)
c.set(0)
c.setValue(N-4,-1)
c.assemble()
ksp = PETSc.KSP()
ksp.create(MPI.COMM_WORLD)
ksp.setType('bicg')
ksp.getPC().setType('jacobi')
ksp.setOperators(A)
ksp.setFromOptions()
PETSc.Comm.Barrier
ksp.solve(c, x)
PETSc.Comm.Barrier

local_x = np.zeros(N)

#print("processors %d"%rank," owns %d,%d"%(id_start,id_end))
local_x[id_start:id_end] = x[...]

global_x = np.zeros(N)
#print(np.array(x))
MPI.COMM_WORLD.Reduce([local_x,MPI.DOUBLE],[global_x,MPI.DOUBLE],op=MPI.SUM,root=0)
if rank == 0:
    print(global_x)
    print(ksp.getResidualNorm())
