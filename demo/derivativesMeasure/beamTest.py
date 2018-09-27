import numpy as np

from openmdao.api import Problem, ScipyOptimizeDriver
import scipy.io as si
from openmdao.test_suite.test_examples.beam_optimization.beam_group import BeamGroup

E = 1E6
L = 1.
b = 0.1
volume = 0.01

num_elements = 50
num_nodes = num_elements + 1
prob = Problem(model=BeamGroup(E=E, L=L, b=b, volume=volume, num_elements=num_elements))

prob.driver = ScipyOptimizeDriver()
prob.driver.options['optimizer'] = 'SLSQP'
prob.driver.options['tol'] = 1e-9
prob.driver.options['disp'] = True

prob.setup()

prob.run_driver()

h = prob['inputs_comp.h']
h = np.hstack([h,h[-1]])
h[1:-1] = (h[0:-2]+h[1:-1])/2
d = prob['displacements_comp.displacements']
dd = d[::2]
print(len(dd))
from util.plot import *
plt.figure(1)
plt = oneDPlot(h,plotstyle='scatter',span=1,xlabel='x',ylabel='values (in meter)',label='thickness')
plt = oneDPlot(dd,plotstyle='plot',span=1,label='displacement')
#finalizePlot(plt1)
finalizePlot(plt, title='Result of thickness and displacement distribution of the beam',savefig=True,fname='tdBeam.eps')

top = dd
bot = dd-h
node_x = np.linspace(0,1,num_elements+1)
plt.figure(2)
plt = twoDPlot(node_x,top,xlabel='x',ylabel='y',label='top',c='b')
plt = twoDPlot(node_x,bot,xlabel='x',ylabel='y',label='bottom',c='orange')
plt.fill_between(node_x,top,bot,facecolor='green',alpha=0.5)
plt.axis('equal')
finalizePlot(plt,title='Optimal shape of the beam',savefig=True,fname='optBeam.eps')