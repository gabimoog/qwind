"""
This module handles the radiation transfer aspects of Qwind.
"""

import numpy as np
from qwind import aux_numba, integral
import qwind.constants as const
from scipy import optimize, integrate


class Radiation():
    """
    This class handles all the calculations involving the radiation field, i.e., radiative opacities, optical depths, radiation force, etc.
    """

    def __init__(self, wind):
        self.wind = wind
        self.r_x = self.ionization_radius()
        self.force_radiation_constant =  3. * self.wind.mdot / (8. * np.pi * self.wind.eta) * (1 - self.wind.fx)
        self.int_hist = []

    def optical_depth_uv(self, r, z, r_0, tau_dr, tau_dr_0):
        """
        UV optical depth.

        Args:
            r: radius in Rg units.
            z: height in Rg units.
            r_0: initial streamline radius.
            tau_dr: charact. optical depth
            tau_dr_0: initial charact. optical depth

        Returns:
            UV optical depth at point (r,z) 
        """
        tau_uv_0 = (r_0 - self.wind.r_init)
        distance = np.sqrt(r**2 + z**2)
        delta_r = r - r_0
        sec_theta = distance / r
        tau_uv = sec_theta *  ( tau_dr_0 * tau_uv_0  +  delta_r * tau_dr )
        return tau_uv
    
    def ionization_parameter(self, r, z, tau_x, rho_shielding):
        """
        Computes Ionization parameter.

        Args:
            r: radius in Rg units.
            z: height in Rg units.
            tau_x: X-Ray optical depth at the point (r,z)
            rho_shielding: density of the atmosphere that contributes to shielding the X-Rays.

        Returns:
            ionization parameter.
        """
        distance_2 = r**2. + z**2.
        xi = self.wind.xray_luminosity * np.exp(-tau_x) / ( rho_shielding * distance_2 * self.wind.Rg**2)
        return xi

    def ionization_radius_kernel(self, rx):
        """
        Auxiliary function to compute ionization radius.

        Args:
            rx: Candidate for radius where material becomes non ionized.

        Returns:
            difference between current ion. parameter and target one.
        """
        ionization_difference = const.ionization_parameter_critical - self.ionization_parameter(rx, 0, 0, self.wind.rho_shielding)
        return ionization_difference

    def ionization_radius(self):
        """
        Computes the disc radius at which xi = xi_0 using the bisect method.
        """
        r_x = optimize.bisect(self.ionization_radius_kernel, self.wind.r_in, self.wind.r_out)
        return r_x 

    def opacity_x_r(self, r):
        """
        X-Ray opacity factor (respect to just Thomson cross-section).

        Args:
            r: radius in Rg

        Returns:
            opacity factor
        """
        if ( r < self.r_x):
            return 1 
        else:
            return 100 

    def optical_depth_x(self, r, z, r_0, tau_dr, tau_dr_0, rho_shielding):
        """
        X-Ray optical depth at a distance d.

        Args:
            r: radius in Rg units.
            z: height in Rg units.
            r_0: initial streamline radius.
            tau_dr: charact. optical depth
            tau_dr_0: initial charact. optical depth
            rho_shielding: atmosphere density contributing to shield the X-Rays.

        Returns:
            X-Ray optical depth at the point (r,z)
        """
        tau_x_0 = (self.r_x - self.wind.r_init) 
        if ( self.r_x < r_0):
            tau_x_0 += 100 * ( r_0 - self.r_x)
        distance = np.sqrt(r ** 2 + z ** 2)
        sec_theta = distance / r
        delta_r = r - r_0
        tau_x = sec_theta * (tau_dr_0 * tau_x_0 + tau_dr * self.opacity_x_r(r) * delta_r)
        return tau_x

    def k(self, xi):
        """
        Auxiliary function required for computing force multiplier.

        Args: 
            xi: Ionisation Parameter.

        Returns:
            Factor k in the force multiplier formula.
        """
        return 0.03 + 0.385 * np.exp(-1.4 * xi**(0.6))

    def eta_max(self, xi):
        """
        Auxiliary function required for computing force multiplier.
        
        Args:
            xi: Ionisation Parameter.
        
        Returns:
            Factor eta_max in the force multiplier formula.
        """
        if(np.log10(xi) < 0.5):
            aux = 6.9 * np.exp(0.16 * xi**(0.4))
            return 10**aux
        else:
            aux = 9.1 * np.exp(-7.96e-3 * xi)
            return 10**aux

    def sobolev_optical_depth(self, tau_dr, dv_dr):
        """
        Returns differential optical depth times a factor that compares thermal velocity with spatial velocity gradient.
        
        Args:
            tau_dr : Charact. optical depth.
            dv_dr : Velocity spatial gradient (dv is in c units, dr is in Rg units).
            T : Wind temperature.

        Returns:
            sobolev optical depth.
        """
        sobolev_length = self.wind.v_thermal / np.abs(dv_dr)
        sobolev_optical_depth = tau_dr * sobolev_length
        return sobolev_optical_depth

    def force_multiplier(self, t, xi):
        """
        Computes the force multiplier, following Stevens & Kallman (1990).
        
        Args:
            t: Sobolev optical depth.
            xi : Ionisation Parameter.

        Returns:
            fm : force multiplier.
        """
        xi = xi / 8.2125 # this factor converts xi to Xi, the other ionization parameter definition which differs by a factor of (4 pi Ryd c).
        k = self.k(xi)
        eta_max = self.eta_max(xi)
        tau_max = t * eta_max
        alpha = 0.6
        if (tau_max < 0.001):
            aux = (1. - alpha) * (tau_max ** alpha)
        else:
            aux = ((1. + tau_max)**(1. - alpha) - 1.) / \
                ((tau_max) ** (1. - alpha))
        fm = k * t**(-alpha) * aux
        return fm

    def force_radiation(self, r, z, fm, tau_uv):
        """
        Computes the radiation force at the point (r,z)

        Args:
            r: radius in Rg units.
            z: height in Rg units.
            fm: force_multiplier
            tau_uv: UV optical depth.

        Returns:
            radiation force at the point (r,z) boosted by fm and attenuated by e^tau_uv.
        """

        if('old_integral' in self.wind.modes):
            i_aux = aux_numba.qwind_integration(r, z)
        elif('non_adaptive' in self.wind.modes):
            i_aux = integral.non_adaptive_integral(r,z, self.wind.r_min, self.wind.r_max)
        else:
            i_aux = aux_numba.qwind_integration_dblquad(r, z, self.wind.r_min, self.wind.r_max)

        abs_uv = np.exp(-tau_uv)
        self.int_hist.append(i_aux)
        force = ( 1 + fm ) * abs_uv * self.force_radiation_constant * np.asarray([i_aux[0], 0., i_aux[1]])
        return force

