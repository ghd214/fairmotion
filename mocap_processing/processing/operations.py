import copy
import math
import numpy as np

from mocap_processing.motion import motion as motion_class
from mocap_processing.utils import constants, conversions, utils

from scipy import stats
from scipy.spatial.transform import Rotation

import warnings

def append(motion1, motion2):
    assert isinstance(motion1, motion_class.Motion)
    assert isinstance(motion2, motion_class.Motion)
    assert motion1.skel.num_joints() == motion2.skel.num_joints()

    combined_motion = copy.deepcopy(motion1)
    combined_motion.name = f"{motion1.name}+{motion2.name}"
    combined_motion.poses.extend(motion2.poses)

    return combined_motion


def transform(motion, T, local=False):
    for pose_id in range(len(motion.poses)):
        R0, p0 = conversions.T2Rp(motion.poses[pose_id].get_root_transform())
        R1, p1 = conversions.T2Rp(T)
        if local:
            R, p = np.dot(R0, R1), p0 + np.dot(R0, p1)
        else:
            R, p = np.dot(R1, R0), p0 + p1
        motion.poses[pose_id].set_root_transform(
            conversions.Rp2T(R, p), local=False,
        )
    return motion


def translate(motion, v, local=False):
    return transform(motion, conversions.p2T(v), local)


def rotate(motion, R, local=False):
    return transform(motion, conversions.R2T(R), local)


def cut(motion, frame_start, frame_end):
    """
    Returns motion object with poses from [frame_start, frame_end) only
    """
    cut_motion = copy.deepcopy(motion)
    cut_motion.name = f"{motion.name}_{frame_start}_{frame_end}"
    cut_motion.poses = motion.poses[frame_start:frame_end]

    return cut_motion


def resample(motion, fps):
    """
    Upsample/downsample frame rate of motion object to `fps` Hz
    """
    poses_new = []

    dt = 1.0 / fps
    t = 0
    while t < motion.fps * len(motion.poses):
        pose = motion.get_pose_by_time(t)
        pose.skel = motion.skel
        poses_new.append(pose)
        t += dt

    motion.poses = poses_new
    motion.fps = fps
    return motion


def position_wrt_root(motion):
    matrix = motion.to_matrix(local=False)
    # Extract positions
    matrix = matrix[:, :, :3, 3]
    # Subtract root position from all joint positions
    matrix = matrix - matrix[:, np.newaxis, 0]
    return matrix


def normalize(v):
    is_list = type(v) == list
    length = np.linalg.norm(v)
    if length > constants.EPSILON:
        norm_v = np.array(v)/length
        if is_list:
            return list(norm_v)
        else:
            return norm_v
    else:
        warnings.warn('!!!The length of input vector is almost zero!!!')
        return v


def slerp(R1, R2, t):
    return np.dot(R1, conversions.A2R(t * conversions.R2A(np.dot(R1.transpose(), R2))))


def lerp(v0, v1, t):
    return v0 + (v1 - v0) * t


def invertT(T):
    R = T[:3, :3]
    p = T[:3, 3]
    invT = constants.eye_T()
    R_trans = R.transpose()
    R_trans_p = np.dot(R_trans, p)
    invT[:3, :3] = R_trans
    invT[:3, 3] = -R_trans_p
    return invT


def Q_op(Q, op, xyzw_in=True):
    """
    change_order:
    normalize:
    halfspace:
    """
    def q2q(q):
        result = q.copy()
        if "normalize" in op:
            norm = np.linalg.norm(result)
            if norm < constants.EPSILON:
                raise Exception("Invalid input with zero length")
            result /= norm
        if "halfspace" in op:
            w_idx = 3 if xyzw_in else 0
            if result[w_idx] < 0.0:
                result *= -1.0
        if "change_order" in op:
            result = result[[3, 0, 1, 2]] if xyzw_in else result[[1, 2, 3, 0]]
        return result

    return utils._apply_fn_agnostic_to_vec_mat(Q, q2q)


def Q_diff(Q1, Q2):
    


def Q_mult(Q1, Q2):
    q1 = Rotation.from_quat(Q1)
    q2 = Rotation.from_quat(Q2)
    return (q1*q2).as_quat()


def Q_closest(Q1, Q2, axis):
    """ 
    This computes optimal-in-place orientation given a target orientation Q1 
    and a geodesic curve (Q2, axis). In tutively speaking, the optimal-in-place 
    orientation is the closest orientation to Q1 when we are able to rotate Q2 
    along the given axis. We assume Q is given in the order of xyzw.
    """
    ws, vs = Q1[3], Q1[0:3]
    w0, v0 = Q2[3], Q2[0:3]
    u = normalize(axis)

    a = ws*w0 + np.dot(vs, v0)
    b = -ws*np.dot(u, v0) + w0*np.dot(vs, u) + np.dot(vs, np.cross(u, v0))
    alpha = math.atan2(a, b)

    theta1 = -2*alpha+math.pi
    theta2 = -2*alpha-math.pi
    G1 = conversions.A2Q(theta1*u)
    G2 = conversions.A2Q(theta2*u)

    if np.dot(Q1, G1) > np.dot(Q1, G2):
        theta = theta1
        Qnearest = Q_mult(G1, Q2)
    else:
        theta = theta2
        Qnearest = Q_mult(G1, Q2)

    return Qnearest, theta


def componentOnVector(inputVector, directionVector):
    return np.inner(directionVector, inputVector) / np.dot(
        directionVector, directionVector
    )


def projectionOnVector(inputVector, directionVector):
    # componentOnVector() * vd
    return componentOnVector(inputVector, directionVector) * directionVector


def R_from_vectors(vec1, vec2):
    """
    Returns R such that R dot vec1 = vec2
    """
    vec1 = normalize(vec1)
    vec2 = normalize(vec2)

    rot_axis = normalize(np.cross(vec1, vec2))
    inner = np.inner(vec1, vec2)
    theta = math.acos(inner)

    if rot_axis[0] == 0 and rot_axis[1] == 0 and rot_axis[2] == 0:
        rot_axis = [0, 1, 0]

    x, y, z = rot_axis
    c = inner
    s = math.sin(theta)
    R = np.array([
        [c + (1.0-c)*x*x, (1.0-c)*x*y - s*z, (1-c)*x*z + s*y],
        [(1.0-c)*x*y + s*z, c + (1.0-c)*y*y, (1.0-c)*y*z - s*x],
        [(1.0-c)*z*x - s*y, (1.0-c)*z*y + s*x, c + (1.0-c)*z*z]
    ])   
    return R


def project_rotation_1D(R, axis):
    """
    Project a 3D rotation matrix to the closest 1D rotation 
    when a rotational axis is given
    """
    Q, angle = Q_closest(conversions.R2Q(R), [1.0, 0.0, 0.0, 0.0], axis)
    return angle


def project_rotation_2D(R, axis1, axis2, order='zyx'):
    """
    Project a 3D rotation matrix to the 2D rotation 
    when two rotational axes are given
    """
    zyx = conversions.R2E(R, order)
    index1 = utils.axis_to_index(axis1)
    index2 = utils.axis_to_index(axis2)
    if index1==0 and index==1: return np.array(zyx[2],zyx[1])
    elif index1==0 and index==2: return np.array(zyx[2],zyx[0])
    elif index1==1 and index==0: return np.array(zyx[1],zyx[2])
    elif index1==1 and index==2: return np.array(zyx[1],zyx[0])
    elif index1==2 and index==0: return np.array(zyx[0],zyx[2])
    elif index1==2 and index==1: return np.array(zyx[0],zyx[1])
    else: raise Exception


def project_rotation_3D(R):
    """
    Project a 3D rotation matrix to the 3D rotation.
    It will just returns corresponding axis-angle.
    """
    return conversions.R2A(R)


def project_angular_vel_1D(w, axis):
    """
    Project a 3D angular velocity to 1d angular velocity.
    """
    return np.linalg.norm(np.dot(w, axis))


def project_angular_vel_2D(w, axis1, axis2):
    """
    Project a 3D angular velocity to 2d angular velocity.
    """
    index1 = utils.axis_to_index(axis1)
    index2 = utils.axis_to_index(axis2)
    return np.array([w[index1],w[index2]])


def project_angular_vel_3D(w):
    """
    Project a 3D angular velocity to 3d angular velocity.
    """
    return w    


def truncnorm(mu, sigma, lower, upper):
    """
    Generate a sample from a truncated normal districution
    """
    return np.atleast_1d(stats.truncnorm(
        (lower - mu) / sigma, (upper - mu) / sigma, 
        loc=mu,
        scale=sigma).rvs())


def random_unit_vector(dim=3):
    """
    Generate a random unit-vector (whose length is 1.0)
    """
    while True:
        v = np.random.uniform(-1.0, 1.0, size=dim)
        l = np.linalg.norm(v)
        if l < constants.EPSILON:
            continue
        v = v / l
        break
    return v


def random_position(mu_l, sigma_l, lower_l, upper_l, dim=3):
    """
    Generate a random position by a truncated normal districution
    """
    l = truncnorm(mu=mu_l,
                  sigma=sigma_l,
                  lower=lower_l,
                  upper=upper_l)
    return random_unit_vector(dim) * l


def random_rotation(mu_theta, sigma_theta, lower_theta, upper_theta):
    """
    Generate a random position by a truncated normal districution
    """
    theta = truncnorm(mu=mu_theta,
                      sigma=sigma_theta,
                      lower=lower_theta,
                      upper=upper_theta)
    return conversions.A2R(random_unit_vector()*theta)


def lerp_from_paired_list(x, xy_pairs, clamp=True):
    """ 
    Given a list of data points in the shape of [[x0,y0][x1,y1],...,[xN,yN]],
    this returns an interpolated y value that correspoinds to a given x value
    """
    x0, y0 = xy_pairs[0]
    xN, yN = xy_pairs[-1]
    # if clamp is false, then check if x is inside of the given x range
    if not clamp:
        assert x0 <= x <= xN
    # Return the boundary values if the value is outside """
    if x <= x0:
        return y0
    elif x >= xN:
        return yN
    else:
        """ Otherwise, return linearly interpolated values """
        for i in range(len(xy_pairs) - 1):
            x1, y1 = xy_pairs[i]
            x2, y2 = xy_pairs[i+1]
            if x1 <= x < x2:
                alpha = (x-x1)/(x2-x1)
                return (1.0-alpha)*y1 + alpha*y2
    raise Exception('This should not be reached!!!')


class Normalizer:
    """
    Helper class for the normalization between two sets of values.
    (real_val_max, real_val_min) <--> (norm_val_max, norm_val_min)
    """
    def __init__(self,
                 real_val_max, real_val_min,
                 norm_val_max, norm_val_min,
                 apply_clamp=True):
        self.set_real_range(real_val_max, real_val_min)
        self.set_norm_range(norm_val_max, norm_val_min)
        self.apply_clamp = apply_clamp
        self.dim = len(real_val_max)

    def set_real_range(self, real_val_max, real_val_min):
        self.real_val_max = real_val_max
        self.real_val_min = real_val_min
        self.real_val_diff = real_val_max - real_val_min
        self.real_val_diff_inv = 1.0 / self.real_val_diff
        #
        # Check if wrong values exist in the setting
        # e.g. min <= max or abs(max-min) is too small
        #
        for v in self.real_val_diff:
            if v <= 0.0 or abs(v) < 1.0e-08:
                raise Exception('Normalizer', 'wrong values')

    def set_norm_range(self, norm_val_max, norm_val_min):
        self.norm_val_max = norm_val_max
        self.norm_val_min = norm_val_min
        self.norm_val_diff = norm_val_max - norm_val_min
        self.norm_val_diff_inv = 1.0 / self.norm_val_diff
        #
        # Check if wrong values exist in the setting
        # e.g. min <= max or abs(max-min) is too small
        #
        for v in self.norm_val_diff:
            if v <= 0.0 or abs(v) < 1.0e-08:
                raise Exception('Normalizer', 'wrong values')

    def real_to_norm(self, val):
        val_0_1 = (val - self.real_val_min) * self.real_val_diff_inv
        if self.apply_clamp:
            self._clip(val_0_1)
        return self.norm_val_min + self.norm_val_diff * val_0_1

    def norm_to_real(self, val):
        val_0_1 = (val - self.norm_val_min) * self.norm_val_diff_inv
        if self.apply_clamp:
            self._clip(val_0_1)
        return self.real_val_min + self.real_val_diff * val_0_1

    def _clip(self, val):
        for i in range(len(val)):
            val[i] = np.clip(val[i], 0.0, 1.0)
