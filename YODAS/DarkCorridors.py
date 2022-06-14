import pandas as pd
import geopandas as gpd
from shapely import wkt
from time import time
import matplotlib.pyplot as plt
import numpy as np
import string
import dijkstar as dj
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree

import warnings
warnings.filterwarnings('ignore')


class LightManager(object):
    def __init__(self, data, city_map_path, street_lights_path, animal_importance = None, max_importance = 0.8, prints = True):
        # max_importance is the maximum weight a factor can have for the optimization
        
        # city zone dataframe (zone, geometry, danger score)
        self.data = data 

        # city_map_path - path to to geojson file of the city map
        self.map = gpd.read_file(city_map_path)
        self.map = self.map.set_crs('EPSG:4326').to_crs('EPSG:27700')

        # street_lights_path - path to CSV file with the street lights of the city (must have a index column without name or with name "ID")
        self.streetlights = pd.read_csv(street_lights_path)
        if 'Unnamed: 0' in self.streetlights.columns:
          self.streetlights.rename(columns = {'Unnamed: 0': 'ID'}, inplace = True)
        elif 'ID' in self.streetlights.columns:
          pass
        else:
          raise Exception('Invalid street lights file!')

        self.streetlights['geometry'] = self.streetlights['geometry'].apply(wkt.loads)
        self.streetlights_gdf = gpd.GeoDataFrame(self.streetlights, crs='EPSG:27700')
        
        # sum scores no minimize total
        if animal_importance != None:
            # if the user specifies a weight for the animal score, them the optimization will give the specified weight for all zones, independently from the max importance
            human_importance = 1 - animal_importance
            self.data['cost'] = animal_importance * self.data.animal_score + human_importance * self.data.human_score
        else:
            # if the user doesn't specify the weight, then it is calculted through the score of the cluster (wich refelcts its importance for the animals)
            # but fisrt the  cluster scores must be changed to be inside the max_importance
            self.data['animal_importance'] = self.data.cluster_score.apply(lambda x: max_importance if x > max_importance else x)
            min_importance = 1 - max_importance
            self.data.animal_importance = self.data.animal_importance.apply(lambda x: min_importance if x < min_importance else x)
            self.data['cost'] = self.data.animal_importance * self.data.animal_score + self.data.animal_importance.apply(lambda x: 1 - x) * self.data.human_score
        
        # dijsktra node network graph for best score path algorithm
        self.graph = dj.Graph()
        
        # zone matrix with scores between adjacent zones (score from zone A to zone B equals score of B, rest filled with NaN)
        self.cost_matrix = pd.DataFrame()
        
        # list with all calculated paths for the object
        self.paths = []
        
        # Dark corridors dataframe
        self.dark_corridors = pd.DataFrame()
        
        # New Lights (empty)
        self.new_lights = None
          
        # Create centroids for zone
        self.data['centroids'] =  self.data['geometry'].centroid
        
        # create clusters dataframe and give each one an ID
        self.clusters = self.data[self.data.contains_cluster == 1]
        self.clusters = gpd.GeoDataFrame(self.clusters[['zone','contains_cluster', 'geometry', 'centroids']])
        self.clusters['cluster'] = list(string.ascii_uppercase)[:len(self.clusters)]
        self.clusters.reset_index(drop=True, inplace = True)
        

    def plot_centroid(self):
        ########
        # Plots centroids for confirmation purposes
        #######
        fig, axs = plt.subplots(1,2, figsize = (15, 15), sharex = True)

        # Original Lighting
        self.map.plot(color='#0A0E42', ax=axs[0])
        self.streetlights_gdf.plot(markersize=0.5, ax=axs[0], color='gold', alpha=0.5)
        axs[0].set_axis_off()
        axs[0].title.set_text('Original Lighting')

        #axs[1].set_aspect('equal')
        self.data.geometry.plot(ax=axs[1], color='white', edgecolor='black')
        self.data.centroid.plot(ax=axs[1], color='red', markersize = 10)

        return fig

    def plot_clusters(self):
      
        ########
        # Plots clusters for confirmation purposes
        #######
        fig, axs = plt.subplots(1,2, figsize = (15, 15), sharex = True)

        # Original Lighting
        self.map.plot(color='#0A0E42', ax=axs[0])
        self.streetlights_gdf.plot(markersize=0.5, ax=axs[0], color='gold', alpha=0.5)
        axs[0].set_axis_off()
        axs[0].title.set_text('Original Lighting')

        #axs[1].set_aspect('equal')
        self.data.geometry.plot(ax=axs[1], color='white', edgecolor='grey')
        self.clusters.centroids.plot(ax=axs[1], color='red', markersize = 30)
        axs[1].set_axis_off()
        axs[1].title.set_text('Clusters')
        fig.tight_layout(pad=5)

        return fig
        
    
    def create_cost_matrix(self):
        #######
        # Creates cost matrix
        #######
        
        cost_matrix = pd.DataFrame(self.data.zone)
        
        counter = 0
        total = len(cost_matrix)
        start = time()
        
        print('Creating Cost Matrix ....')
        for row in self.data.itertuples():
            zone = row.zone
            
            # Find zone neighbours
            neighbours = self.data[self.data.touches(row.geometry)]
            
            # Merge scores of neigbours with cost matrix (non-neigbours filled with NaN)
            temp_score = neighbours[['zone', 'cost']].rename(columns={'cost':zone})
            cost_matrix = cost_matrix.merge(temp_score, on='zone', how='left')
        

            end = time()
            current = round((end-start)/60, 2)
            counter += 1
            print (f' {counter} of {total} | {current} of estimated {round(current/counter * total, 2)} min ...', end='\r')

        # update class properties
        self.cost_matrix = cost_matrix.copy()
        
        print('')
        return cost_matrix
    
    def create_graphs(self):
        #######
        # Creates Dijsktra's graph from cost matrix
        #######
        
        # Check if matrixs are set
        if self.cost_matrix.empty:
            raise ValueError("Cost Matrix is empty. Create Matrix first!")
 
        print('Creating graphs ....', end='\r')
        
        # Create aux dfs for each matrix/ graph
        aux = self.cost_matrix.copy()
  
        # Get nodes/ zones from any cost matrix
        nodes = aux.drop(columns=['zone'])
        nodes = nodes.columns.to_list()
        
        # Iterate over nodes, type of cost_matrixes and the matrix itself (while dropping non-neighbours)
        counter = 0
        total = len(nodes)
        # For each zone
        for zone in nodes:
            # Drop non-neighbours of current zone
            temp = pd.DataFrame(aux[zone].dropna())
            # For each neighbour add leg between current zone and neighbour  
            for neighbour, cost in temp.itertuples():
                self.graph.add_edge(int(zone), int(neighbour), cost)
            counter += 1
            print(f'Created {counter} of {total} nodes',end='\r')
        
        print('Graphs created successfully!')
        
    def find_path(self, start_zone, end_zone, ID, prints = True):
        #######
        # Finds best path according to Dijsktra's algorithm
        #######
        if prints:
            print('Finding path ....', end='\r')
        
        # new path
        path = {'path': [], 'score': 0, 'ID' : ID}
    
        # Find path
        try:
            path['path'] = dj.find_path(self.graph, start_zone, end_zone).nodes
        except:
            if prints:
                print(f'Unable to find path between zones {start_zone} and {end_zone}')           
            else:
                pass
 
        # Calculate distances, average and maximum danger scores for each path
        total_score = self.data[self.data.zone.isin(path['path'])].cost.sum()
        path['score'] = round(total_score,2)
        
        self.paths.append(path)

        return path
    
    def find_cluster_paths(self, plots = True):
        cl_1 = 0
        while cl_1 < len(self.clusters) - 1:
            zone_1 = self.clusters.at[cl_1, 'zone']
            cluster_1 = self.clusters.at[cl_1, 'cluster']
            cl_2 = cl_1 + 1
            while cl_2 < len(self.clusters):
                zone_2 = self.clusters.at[cl_2, 'zone']
                cluster_2 = self.clusters.at[cl_2, 'cluster']
                self.find_path(zone_1, zone_2, f'{cluster_1}{cluster_2}')
                cl_2 += 1
            cl_1 += 1
            
        if plots:

            # Plot all paths
            self.plot_paths()

        return pd.DataFrame(self.paths)
    
    def create_dark_corridors(self):
        
        # Create paths dataframe
        paths = pd.DataFrame(self.paths)
        aux = paths.ID.apply(lambda x: pd.Series(list(x)))
        paths['1'] = aux[0]
        paths['2'] = aux[1]
        
        matrix_n = len(self.clusters)
        matrix  = []
        column = 1
        for cl in self.clusters.itertuples():
            row = []
            # fill row with 0 for lower diagonal
            column_counter = 0
            while column_counter < column:
                row.append(0)
                column_counter += 1

            aux = paths[paths['1'] == cl.cluster]
            for score in aux.itertuples():
                row.append(score.score)

            matrix.append(row)
            column += 1

        X = csr_matrix(matrix)
        Tcsr = minimum_spanning_tree(X)
        tree = Tcsr.toarray()
        tree.astype(int)
        
        # final paths
        row = 0
        IDs = []
        while row < len(tree):
            column = 0
            while column < len(tree[row]):
                if tree[row][column] > 0:
                    IDs.append(paths[(paths['1']== self.clusters.at[row, 'cluster']) & (paths['2']== self.clusters.at[column, 'cluster'])].reset_index().at[0, 'ID'])
                column += 1
            row += 1

        self.dark_corridors = paths[paths.ID.isin(IDs)]  
        self.dark_corridors.reset_index(drop=True, inplace=True)
        return self.dark_corridors
    
    def update_lighting(self, plots = True):
        
        no_light_zones = []
        for row in self.dark_corridors.itertuples():
            no_light_zones += row.path
        zonno_light_zoneses = np.array(no_light_zones)
        no_light_zones = np.unique(no_light_zones)
        no_light_zones = list(no_light_zones)
        
        turnoff_zones = self.data[self.data.zone.isin(no_light_zones)]

        turnoff_lights = []
        aux = self.streetlights.copy()
        counter = 1
        total = len(turnoff_zones)
        for zone in turnoff_zones.itertuples():
            temp = gpd.GeoSeries(self.streetlights.geometry)
            temp = pd.DataFrame(temp.within(zone.geometry))
            temp = temp[temp[0] == True]
            turnoff_lights += list(temp.index)
            print(f'{counter} of {total}', end='\r')
            counter += 1

        self.new_lights = self.streetlights[~self.streetlights.ID.isin(turnoff_lights)]
        self.new_lights = gpd.GeoDataFrame(self.new_lights, crs='EPSG:27700')
        
        if plots:
            # plot lighting
            fig, axs = plt.subplots(1,2, figsize = (15, 15), sharex = True)

            # Original Lighting
            self.map.plot(color='#0A0E42', ax=axs[0])
            self.streetlights_gdf.plot(markersize=0.5, ax=axs[0], color='gold', alpha=0.5)
            axs[0].set_axis_off()
            axs[0].title.set_text('Original Lighting')

            # Dark Corridors
            self.map.plot(color='#0A0E42', ax=axs[1])
            self.new_lights.plot(markersize=0.5, ax=axs[1], color='gold', alpha=0.5)
            axs[1].set_axis_off()
            axs[1].title.set_text('Lighting W/ Dark Corridors')
            # Cluster zones
            self.clusters.centroids.plot(ax=axs[1], color='green', markersize = 40, label = 'Bat Clusters Centers')
            axs[1].legend(loc="upper right")
            fig.tight_layout(pad=5)
        
        return self.new_lights, fig
        
    
    def plot_paths(self, add_legend=False):
        #######
        # Plots all calculated paths
        #######
        
        # Reset route type in dataframe
        try:
            self.data.drop(inplace=True, columns={'route_type'})
        except:
            pass
        
        # Create route type in dataframe to label plot
        for path in self.paths:
            self.data.loc[self.data.zone.isin(path['path']), 'route_type'] = f"Path {path['ID']} | Score: {path['score']}"
        
        # Plot
        fig, axs = plt.subplots(1,2, figsize = (15, 15), sharex = True)

        # Original Lighting
        self.map.plot(color='#0A0E42', ax=axs[0])
        self.streetlights_gdf.plot(markersize=0.5, ax=axs[0], color='gold', alpha=0.5)
        axs[0].set_axis_off()
        axs[0].title.set_text('Original Lighting')

        self.data.plot(ax=axs[1], color='white', edgecolor='black')
        self.data.plot(column='route_type', ax=axs[1], legend=add_legend)
        axs[1].set_axis_off()
        axs[1].title.set_text('Calculated paths')
        fig.tight_layout(pad=5)

        return fig

    
    def save_cost_matrix(self, filename = 'cost_matrix.csv'):
        #######
        # Saves cost matrix as .csv
        #######

        print('Saving file',end='\r')
        self.cost_matrix.to_csv(filename)

        print('Saved files successfully!')
        
    def load_cost_matrix(self, filename = 'cost_matrix.csv'):
        #######
        # Loads main cost matrixes (distance) from .csv
        #######
        
        try:
            print(f'Loading file',end='\r')
            self.cost_matrix = pd.read_csv(filename)
            self.cost_matrix.drop(inplace=True, columns=['Unnamed: 0'])
            print('Loaded file successfully!')
            
            self.create_graphs()
        
        except:
            print('Unable to load files! Confirm file existance.')
    
    def build_cost_matrix(self, save_file):
        #######
        # Builds object from scratch without loading information
        #######
        self.create_cost_matrix()
        self.save_cost_matrix(filename=save_file)        
    
