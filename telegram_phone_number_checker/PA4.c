#include <stdlib.h>
#include <stdio.h>
#include <mpi.h>
#include <time.h>

void display(int* global_grid, int n, int t, int rank, int size);
void exchange_ghost_cells(int* grid, int local_n, MPI_Comm cart_comm);
void update_grid(int* grid, int* buffer, int local_n);

int main(int argc, char** argv) {
    int n = 5; // Hardcoded grid size
    int gens = 3; // Hardcoded number of generations

    MPI_Init(&argc, &argv);

    int rank, size;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);

    // Seed the random number generator
    srand(time(NULL) + rank);

    // Create a 2D Cartesian communicator
    int dims[2] = {0, 0};
    MPI_Dims_create(size, 2, dims);
    int periods[2] = {1, 1}; // periodic boundary conditions
    MPI_Comm cart_comm;
    MPI_Cart_create(MPI_COMM_WORLD, 2, dims, periods, 1, &cart_comm);

    int coords[2];
    MPI_Cart_coords(cart_comm, rank, 2, coords);

    // Ensure the grid size is divisible by the number of ranks in each dimension
    if (n % dims[0] != 0 || n % dims[1] != 0) {
        if (rank == 0) {
            fprintf(stderr, "Error: Grid size must be divisible by the number of ranks in each dimension.\n");
        }
        MPI_Finalize();
        return EXIT_FAILURE;
    }

    // Local grid size (excluding ghost cells)
    int local_n = n / dims[0];
    int local_size = (local_n + 2) * (local_n + 2); // including ghost cells

    // Allocate grids (including space for ghost cells)
    int* grid = (int*)calloc(local_size, sizeof(int));
    int* buffer = (int*)calloc(local_size, sizeof(int));
    int* global_grid = NULL;
    if (rank == 0) {
        global_grid = (int*)calloc(n * n, sizeof(int));
    }
    if (grid == NULL || buffer == NULL || (rank == 0 && global_grid == NULL)) {
        fprintf(stderr, "Error: Unable to allocate memory for grids.\n");
        MPI_Finalize();
        return EXIT_FAILURE;
    }

    // Initialize the grid randomly for each run
    for (int i = 1; i <= local_n; i++) {
        for (int j = 1; j <= local_n; j++) {
            grid[i * (local_n + 2) + j] = rand() % 2;
        }
    }

    // Main loop over generations
    for (int t = 1; t <= gens; t++) {
        // Perform halo exchange
        exchange_ghost_cells(grid, local_n, cart_comm);

        // Update the grid based on the Game of Life rules
        update_grid(grid, buffer, local_n);

        // Gather local grids at rank 0
        int* sendbuf = (int*)malloc(local_n * local_n * sizeof(int));
        for (int i = 1; i <= local_n; i++) {
            for (int j = 1; j <= local_n; j++) {
                sendbuf[(i - 1) * local_n + (j - 1)] = grid[i * (local_n + 2) + j];
            }
        }
        MPI_Gather(sendbuf, local_n * local_n, MPI_INT, global_grid, local_n * local_n, MPI_INT, 0, MPI_COMM_WORLD);
        free(sendbuf);

        // Synchronize all processes before displaying the grid
        MPI_Barrier(cart_comm);

        // Display the global grid from rank 0
        if (rank == 0) {
            display(global_grid, n, t, rank, size);
        }

        // Copy the buffer back to the grid
        for (int i = 1; i <= local_n; i++) {
            for (int j = 1; j <= local_n; j++) {
                grid[i * (local_n + 2) + j] = buffer[i * (local_n + 2) + j];
            }
        }
    }

    free(grid);
    free(buffer);
    if (rank == 0) {
        free(global_grid);
    }

    MPI_Finalize();
    return 0;
}

void display(int* global_grid, int n, int t, int rank, int size) {
    printf("Iteration: %d\n", t);
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            printf("%d ", global_grid[i * n + j]);
        }
        printf("\n");
    }
    printf("\n");
}

void exchange_ghost_cells(int* grid, int n, MPI_Comm cart_comm) {
    MPI_Status status;
    MPI_Request request[8];
    int left, right, up, down;
    MPI_Cart_shift(cart_comm, 1, 1, &left, &right);
    MPI_Cart_shift(cart_comm, 0, 1, &up, &down);

    // Send/Receive left and right columns
    MPI_Isend(&grid[1 * (n + 2) + 1], n, MPI_INT, left, 0, cart_comm, &request[0]);
    MPI_Irecv(&grid[1 * (n + 2) + (n + 1)], n, MPI_INT, right, 0, cart_comm, &request[1]);
    MPI_Isend(&grid[1 * (n + 2) + n], n, MPI_INT, right, 1, cart_comm, &request[2]);
    MPI_Irecv(&grid[1 * (n + 2) + 0], n, MPI_INT, left, 1, cart_comm, &request[3]);

    // Send/Receive top and bottom rows
    MPI_Isend(&grid[1 * (n + 2) + 1], n, MPI_INT, up, 2, cart_comm, &request[4]);
    MPI_Irecv(&grid[(n + 1) * (n + 2) + 1], n, MPI_INT, down, 2, cart_comm, &request[5]);
    MPI_Isend(&grid[n * (n + 2) + 1], n, MPI_INT, down, 3, cart_comm, &request[6]);
    MPI_Irecv(&grid[0 * (n + 2) + 1], n, MPI_INT, up, 3, cart_comm, &request[7]);

    MPI_Waitall(8, request, MPI_STATUSES_IGNORE);
}

void update_grid(int* grid, int* buffer, int local_n) {
    for (int i = 1; i <= local_n; i++) {
        for (int j = 1; j <= local_n; j++) {
            int live_neighbors = grid[(i - 1) * (local_n + 2) + (j - 1)] +
                                 grid[(i - 1) * (local_n + 2) + j] +
                                 grid[(i - 1) * (local_n + 2) + (j + 1)] +
                                 grid[i * (local_n + 2) + (j - 1)] +
                                 grid[i * (local_n + 2) + (j + 1)] +
                                 grid[(i + 1) * (local_n + 2) + (j - 1)] +
                                 grid[(i + 1) * (local_n + 2) + j] +
                                 grid[(i + 1) * (local_n + 2) + (j + 1)];

            
            if (grid[i * (local_n + 2) + j] == 1) {
                if (live_neighbors < 2 || live_neighbors > 3) {
                    buffer[i * (local_n + 2) + j] = 0; // Dies
                } else {
                    buffer[i * (local_n + 2) + j] = 1; // Lives
                }
            } else {
                if (live_neighbors == 3) {
                    buffer[i * (local_n + 2) + j] = 1; // Comes to life
                } else {
                    buffer[i * (local_n + 2) + j] = 0; // Stays dead
                }
            }
        }
    }
}

